from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import tickets as ticket_repo
from app.repositories import prices as price_repo
from app.repositories import batches as batch_repo
from app.repositories import skus as sku_repo

REVERT_STRATEGIES = ("list_price", "previous_price")
DEFAULT_REVERT_STRATEGY = "list_price"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_clock() -> dict[str, Any]:
    conn = connect()
    try:
        return {
            "business_clock": settings_repo.get(conn, "business_clock"),
            "clock_label": settings_repo.get(conn, "clock_label"),
            "revert_strategy": _revert_strategy(conn),
        }
    finally:
        conn.close()


def set_clock(clock_at: str) -> dict[str, Any]:
    clock_at = (clock_at or "").strip()
    if not clock_at:
        raise HTTPException(400, "业务钟不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_clock", clock_at)
        conn.commit()
        return get_clock()
    finally:
        conn.close()


def _revert_strategy(conn) -> str:
    value = settings_repo.get(conn, "revert_strategy", DEFAULT_REVERT_STRATEGY)
    return value if value in REVERT_STRATEGIES else DEFAULT_REVERT_STRATEGY


def set_revert_strategy(strategy: str) -> dict[str, Any]:
    strategy = (strategy or "").strip()
    if strategy not in REVERT_STRATEGIES:
        raise HTTPException(
            400,
            f"回落目标策略只能是 {' / '.join(REVERT_STRATEGIES)}（全局一种，互斥）",
        )
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "revert_strategy", strategy)
        conn.commit()
        return {"revert_strategy": strategy}
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def apply_due() -> dict[str, Any]:
    """Apply all scheduled tickets whose effective_at <= business clock.

    Mutually exclusive with revert: while any applied ticket is past its
    effective_until (pending revert), applying is rejected. Scheduled tickets
    whose cutoff has already passed are marked expired and never written to
    the book as a promo price.
    """
    conn = connect(immediate=True)
    try:
        clock = settings_repo.get(conn, "business_clock")
        if not clock:
            raise HTTPException(400, "业务钟未设置")

        pending_revert = ticket_repo.applied_past_cutoff(conn, clock)
        if pending_revert:
            nos = "、".join(t["ticket_no"] for t in pending_revert)
            raise HTTPException(
                409,
                f"改价单 {nos} 已过生效截止且未回落，须先执行回落批次；回落中再次应用被拒",
            )

        due = ticket_repo.due_tickets(conn, clock)
        expired = [t for t in due if t["effective_until"] and t["effective_until"] <= clock]
        applicable = [
            t for t in due if not (t["effective_until"] and t["effective_until"] <= clock)
        ]
        for t in expired:
            ticket_repo.mark_expired(conn, t["id"])

        if not applicable:
            conn.commit()
            return {
                "applied": False,
                "message": "当前业务钟下没有到期改价单",
                "business_clock": clock,
                "ticket_count": 0,
                "line_count": 0,
                "expired": [t["ticket_no"] for t in expired],
            }

        batch_no = batch_repo.next_batch_no(conn, "apply")
        created = _now()
        line_total = 0
        applied_tickets = []
        # create batch first with zeros, update after
        batch_id = batch_repo.create_batch(
            conn,
            batch_no=batch_no,
            kind="apply",
            clock_at=clock,
            ticket_count=0,
            line_count=0,
            status="running",
            created_at=created,
        )

        for t in applicable:
            lines = ticket_repo.list_lines(conn, t["id"])
            for line in lines:
                price_repo.upsert_book(
                    conn,
                    sku_code=line["sku_code"],
                    price=float(line["new_price"]),
                    ticket_no=t["ticket_no"],
                    effective_at=t["effective_at"],
                    updated_at=created,
                    source_kind="apply",
                )
                line_total += 1
            ticket_repo.mark_applied(conn, t["id"], applied_at=created, batch_id=batch_id)
            applied_tickets.append(t["ticket_no"])

        conn.execute(
            "UPDATE apply_batches SET ticket_count=?, line_count=?, status=? WHERE id=?",
            (len(applicable), line_total, "done", batch_id),
        )
        conn.commit()
        return {
            "applied": True,
            "batch_no": batch_no,
            "business_clock": clock,
            "ticket_count": len(applicable),
            "line_count": line_total,
            "tickets": applied_tickets,
            "expired": [t["ticket_no"] for t in expired],
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revert_expired() -> dict[str, Any]:
    """Revert applied tickets whose effective_until <= business clock.

    One revert batch writes the price book and price history atomically
    (same transaction: all succeed or all roll back). The revert target is
    the single global strategy: list price, or the last successful price
    before the ticket took effect (falling back to list price).
    """
    conn = connect(immediate=True)
    try:
        clock = settings_repo.get(conn, "business_clock")
        if not clock:
            raise HTTPException(400, "业务钟未设置")
        strategy = _revert_strategy(conn)

        due = ticket_repo.applied_past_cutoff(conn, clock)
        if not due:
            conn.commit()
            return {
                "reverted": False,
                "message": "当前业务钟下没有需要回落的改价单",
                "business_clock": clock,
                "strategy": strategy,
                "ticket_count": 0,
                "line_count": 0,
            }

        batch_no = batch_repo.next_batch_no(conn, "revert")
        created = _now()
        line_total = 0
        reverted_tickets = []
        batch_id = batch_repo.create_batch(
            conn,
            batch_no=batch_no,
            kind="revert",
            clock_at=clock,
            ticket_count=0,
            line_count=0,
            status="running",
            created_at=created,
        )

        for t in due:
            lines = ticket_repo.list_lines(conn, t["id"])
            for line in lines:
                sku = sku_repo.get_by_code(conn, line["sku_code"])
                if not sku:
                    raise HTTPException(500, f"商品 {line['sku_code']} 不存在，回落中止")
                if strategy == "previous_price":
                    prev = price_repo.previous_price_before(
                        conn, line["sku_code"], t["effective_at"]
                    )
                    if prev:
                        target = float(prev["price"])
                        source_no = prev["source_ticket_no"]
                    else:
                        target = float(sku["list_price"])
                        source_no = None
                else:
                    target = float(sku["list_price"])
                    source_no = None
                price_repo.upsert_book(
                    conn,
                    sku_code=line["sku_code"],
                    price=target,
                    ticket_no=source_no,
                    effective_at=t["effective_until"],
                    updated_at=created,
                    source_kind="revert",
                )
                line_total += 1
            ticket_repo.mark_reverted(conn, t["id"], reverted_at=created, batch_id=batch_id)
            reverted_tickets.append(t["ticket_no"])

        conn.execute(
            "UPDATE apply_batches SET ticket_count=?, line_count=?, status=? WHERE id=?",
            (len(due), line_total, "done", batch_id),
        )
        conn.commit()
        return {
            "reverted": True,
            "batch_no": batch_no,
            "business_clock": clock,
            "strategy": strategy,
            "ticket_count": len(due),
            "line_count": line_total,
            "tickets": reverted_tickets,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
