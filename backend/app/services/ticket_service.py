from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import skus as sku_repo
from app.repositories import tickets as ticket_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_tickets() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return ticket_repo.list_tickets(conn)
    finally:
        conn.close()


def get_ticket(ticket_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        t = ticket_repo.get_ticket(conn, ticket_id)
        if not t:
            raise HTTPException(404, "改价单不存在")
        t["lines"] = ticket_repo.list_lines(conn, ticket_id)
        return t
    finally:
        conn.close()


def create_ticket(body: dict[str, Any]) -> dict[str, Any]:
    title = (body.get("title") or "").strip()
    effective_at = (body.get("effective_at") or "").strip()
    effective_until = (body.get("effective_until") or "").strip() or None
    note = (body.get("note") or None)
    lines = body.get("lines") or []
    if not title:
        raise HTTPException(400, "标题必填")
    if not effective_at:
        raise HTTPException(400, "生效时刻必填")
    if effective_until and effective_until <= effective_at:
        raise HTTPException(400, "生效截止须晚于生效时刻")
    if not lines:
        raise HTTPException(400, "至少一行改价明细")

    conn = connect(immediate=True)
    try:
        ticket_no = ticket_repo.next_ticket_no(conn)
        tid = ticket_repo.create_ticket(
            conn,
            ticket_no=ticket_no,
            title=title,
            effective_at=effective_at,
            effective_until=effective_until,
            note=note,
            created_at=_now(),
        )
        for line in lines:
            sku_code = (line.get("sku_code") or "").strip()
            new_price = line.get("new_price")
            if not sku_code:
                raise HTTPException(400, "明细缺少 sku_code")
            sku = sku_repo.get_by_code(conn, sku_code)
            if not sku:
                raise HTTPException(400, f"未知商品 {sku_code}")
            book = conn.execute(
                "SELECT current_price FROM price_book WHERE sku_code=?", (sku_code,)
            ).fetchone()
            old_price = float(book["current_price"]) if book else float(sku["list_price"])
            if new_price is None:
                raise HTTPException(400, f"{sku_code} 缺少 new_price")
            ticket_repo.add_line(
                conn,
                ticket_id=tid,
                sku_code=sku_code,
                old_price=old_price,
                new_price=float(new_price),
            )
        conn.commit()
        return get_ticket(tid)
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_ticket(ticket_id: int) -> dict[str, Any]:
    """Void a ticket. Voiding before its cutoff cancels any future revert;
    once the cutoff has passed the ticket must go through a revert batch."""
    conn = connect(immediate=True)
    try:
        t = ticket_repo.get_ticket(conn, ticket_id)
        if not t:
            raise HTTPException(404, "改价单不存在")
        status = t["status"]
        if status == "void":
            raise HTTPException(409, "改价单已作废")
        if status == "reverted":
            raise HTTPException(409, "改价单已回落，不能作废")
        if status == "expired":
            raise HTTPException(409, "改价单已过期未应用，不能作废")
        clock = settings_repo.get(conn, "business_clock")
        if (
            status == "applied"
            and t["effective_until"]
            and clock
            and clock >= t["effective_until"]
        ):
            raise HTTPException(409, "已过生效截止，须走回落批次，不能作废")
        ticket_repo.mark_void(conn, ticket_id, voided_at=_now())
        conn.commit()
        return get_ticket(ticket_id)
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
