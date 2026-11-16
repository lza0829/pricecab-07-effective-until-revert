"""E2E flow: 设截止 → 应用 → 拨钟过截止 → 回落，以及各互斥/一致性约束。

Runs against a fresh SQLite DB in a temp DATA_DIR, through the real FastAPI app.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="pricecab-test-")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.config import DB_PATH  # noqa: E402

client = TestClient(app)

T0 = "2026-11-16T09:00:00Z"
T1 = "2026-11-16T10:30:00Z"
T2 = "2026-11-16T13:00:00Z"
T3 = "2026-11-16T14:30:00Z"
T4 = "2026-11-16T16:00:00Z"


def set_clock(ts: str) -> None:
    r = client.put("/api/apply/clock", json={"business_clock": ts})
    assert r.status_code == 200, r.text


def create_ticket(title, eff, until, sku, price):
    body = {
        "title": title,
        "effective_at": eff,
        "effective_until": until,
        "lines": [{"sku_code": sku, "new_price": price}],
    }
    r = client.post("/api/tickets", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def book() -> dict:
    return {r["sku_code"]: r for r in client.get("/api/prices/book").json()}


def history(sku: str) -> list[dict]:
    return client.get(f"/api/prices/history?sku_code={sku}").json()


def price_at(sku: str, at: str):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT price FROM price_history WHERE sku_code=? AND effective_at <= ? "
            "ORDER BY effective_at DESC, id DESC LIMIT 1",
            (sku, at),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_01_setup_and_create_with_cutoff():
    set_clock(T0)
    t1 = create_ticket("午间茶饮促销", "2026-11-16T10:00:00Z", "2026-11-16T12:00:00Z", "SKU-TEA-001", 19.9)
    assert t1["effective_until"] == "2026-11-16T12:00:00Z"
    assert t1["status"] == "scheduled"
    t3 = create_ticket("海苔限时特价", "2026-11-16T10:00:00Z", "2026-11-16T12:00:00Z", "SKU-SNK-011", 9.9)
    assert t3["status"] == "scheduled"


def test_02_cutoff_must_be_after_effective():
    r = client.post(
        "/api/tickets",
        json={
            "title": "坏单",
            "effective_at": "2026-11-16T10:00:00Z",
            "effective_until": "2026-11-16T10:00:00Z",
            "lines": [{"sku_code": "SKU-TEA-001", "new_price": 1.0}],
        },
    )
    assert r.status_code == 400


def test_03_apply_before_cutoff():
    set_clock(T1)
    r = client.post("/api/apply/due")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] and body["ticket_count"] == 2
    b = book()
    assert b["SKU-TEA-001"]["current_price"] == 19.9
    assert b["SKU-TEA-001"]["source_kind"] == "apply"
    assert b["SKU-SNK-011"]["current_price"] == 9.9
    # 再点一次：没有到期单
    r = client.post("/api/apply/due")
    assert r.json()["applied"] is False


def test_04_void_before_cutoff_cancels_future_revert():
    tickets = client.get("/api/tickets").json()
    t3 = next(t for t in tickets if t["title"] == "海苔限时特价")
    r = client.post(f"/api/tickets/{t3['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"


def test_05_apply_rejected_while_revert_pending():
    set_clock(T2)  # 越过 12:00 截止
    r = client.post("/api/apply/due")
    assert r.status_code == 409, r.text
    assert "回落" in r.json()["detail"]
    # 促销价仍在账上，未被应用流程触碰
    assert book()["SKU-TEA-001"]["current_price"] == 19.9
    dash = client.get("/api/dashboard").json()
    assert dash["due_revert"] == 1  # 作废的 T3 不算待回落


def test_06_revert_to_list_price_writes_book_and_history():
    r = client.post("/api/apply/revert")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reverted"] and body["ticket_count"] == 1
    assert body["strategy"] == "list_price"
    assert body["batch_no"] == "RB-0001"

    b = book()
    assert b["SKU-TEA-001"]["current_price"] == 28.0  # 牌价
    assert b["SKU-TEA-001"]["source_kind"] == "revert"
    assert b["SKU-TEA-001"]["source_ticket_no"] is None  # 牌价回落
    assert b["SKU-TEA-001"]["effective_at"] == "2026-11-16T12:00:00Z"
    # 作废单不走回落：促销价留在账上
    assert b["SKU-SNK-011"]["current_price"] == 9.9

    # 历史末行 = 回落行 = 价签现行价
    h = history("SKU-TEA-001")
    last = h[0]  # API 按 id DESC
    assert last["price"] == 28.0
    assert last["source_kind"] == "revert"
    assert last["effective_at"] == "2026-11-16T12:00:00Z"
    assert b["SKU-TEA-001"]["current_price"] == last["price"]

    # 对账回溯一致：促销窗口内是促销价，截止后是牌价
    assert price_at("SKU-TEA-001", "2026-11-16T11:00:00Z") == 19.9
    assert price_at("SKU-TEA-001", "2026-11-16T12:00:00Z") == 28.0
    assert price_at("SKU-TEA-001", "2026-11-16T13:00:00Z") == 28.0

    dash = client.get("/api/dashboard").json()
    assert dash["due_revert"] == 0
    assert dash["reverted_count"] == 1


def test_07_after_revert_apply_never_touches_original_ticket():
    r = client.post("/api/apply/due")
    assert r.status_code == 200
    assert r.json()["applied"] is False
    tickets = client.get("/api/tickets").json()
    t1 = next(t for t in tickets if t["title"] == "午间茶饮促销")
    assert t1["status"] == "reverted"
    assert t1["revert_batch_id"]
    assert book()["SKU-TEA-001"]["current_price"] == 28.0
    # 已回落单不能作废
    r = client.post(f"/api/tickets/{t1['id']}/void")
    assert r.status_code == 409


def test_08_revert_when_nothing_pending():
    r = client.post("/api/apply/revert")
    assert r.status_code == 200
    assert r.json()["reverted"] is False


def test_09_strategy_is_global_and_validated():
    r = client.put("/api/settings/revert-strategy", json={"strategy": "bogus"})
    assert r.status_code == 400
    r = client.put("/api/settings/revert-strategy", json={"strategy": "previous_price"})
    assert r.status_code == 200
    assert client.get("/api/apply/clock").json()["revert_strategy"] == "previous_price"


def test_10_previous_price_chain_and_fallback():
    # T4：红茶礼盒 50，无截止（上一单）；T5：40，带截止；T7：冰淇淋 10，带截止（无上一单 → 回落牌价）
    t4 = create_ticket("红茶礼盒调价", "2026-11-16T13:30:00Z", None, "SKU-TEA-002", 50.0)
    t5 = create_ticket("红茶礼盒闪购", "2026-11-16T14:00:00Z", "2026-11-16T15:00:00Z", "SKU-TEA-002", 40.0)
    t7 = create_ticket("冰淇淋秒杀", "2026-11-16T14:00:00Z", "2026-11-16T15:00:00Z", "SKU-FRZ-032", 10.0)

    set_clock(T3)
    r = client.post("/api/apply/due")
    assert r.json()["applied"] and r.json()["ticket_count"] == 3
    assert book()["SKU-TEA-002"]["current_price"] == 40.0

    set_clock(T4)
    r = client.post("/api/apply/due")
    assert r.status_code == 409  # 回落中再次应用被拒

    r = client.post("/api/apply/revert")
    body = r.json()
    assert body["reverted"] and body["ticket_count"] == 2
    assert body["batch_no"] == "RB-0002"

    b = book()
    # T5 回落到截止前上一有效价 = T4 的 50，来源单号指向上一单
    assert b["SKU-TEA-002"]["current_price"] == 50.0
    assert b["SKU-TEA-002"]["source_kind"] == "revert"
    assert b["SKU-TEA-002"]["source_ticket_no"] == t4["ticket_no"]
    # T7 无上一单 → 回落牌价，来源记为牌价回落
    assert b["SKU-FRZ-032"]["current_price"] == 15.5
    assert b["SKU-FRZ-032"]["source_kind"] == "revert"
    assert b["SKU-FRZ-032"]["source_ticket_no"] is None

    h = history("SKU-TEA-002")[0]
    assert h["price"] == 50.0 and h["source_kind"] == "revert"
    assert h["effective_at"] == "2026-11-16T15:00:00Z"
    assert price_at("SKU-TEA-002", "2026-11-16T14:30:00Z") == 40.0
    assert price_at("SKU-TEA-002", "2026-11-16T16:00:00Z") == 50.0

    tickets = {t["ticket_no"]: t for t in client.get("/api/tickets").json()}
    assert tickets[t5["ticket_no"]]["status"] == "reverted"
    assert tickets[t4["ticket_no"]]["status"] == "applied"  # 无截止，不回落


def test_11_scheduled_ticket_past_cutoff_goes_expired_never_promo():
    # 业务钟 16:00，建一张生效/截止都已过去的单：不得按促销价应用成功
    t8 = create_ticket("过期抽纸促销", "2026-11-16T10:00:00Z", "2026-11-16T11:00:00Z", "SKU-DAY-022", 25.0)
    r = client.post("/api/apply/due")
    body = r.json()
    assert r.status_code == 200
    assert body["applied"] is False
    assert t8["ticket_no"] in body["expired"]
    assert book()["SKU-DAY-022"]["current_price"] == 32.0  # 牌价未被触碰
    tickets = {t["ticket_no"]: t for t in client.get("/api/tickets").json()}
    assert tickets[t8["ticket_no"]]["status"] == "expired"
    # 过期单不能作废、也不产生待回落
    r = client.post(f"/api/tickets/{t8['id']}/void")
    assert r.status_code == 409
    assert client.get("/api/dashboard").json()["due_revert"] == 0


def test_12_batches_and_history_consistent():
    batches = client.get("/api/apply/batches").json()
    applies = [b for b in batches if b["kind"] == "apply"]
    reverts = [b for b in batches if b["kind"] == "revert"]
    assert [b["batch_no"] for b in reversed(reverts)] == ["RB-0001", "RB-0002"]
    assert len(applies) == 2
    assert all(b["status"] == "done" for b in batches)
    # 每个回落行都与价签现行价一致
    b = book()
    for sku, row in b.items():
        h = history(sku)[0]
        assert h["price"] == row["current_price"], sku
        assert h["source_kind"] == row["source_kind"], sku
    # 对账接口仍可回溯
    rec = client.get("/api/reconcile/sales").json()
    assert rec["sale_count"] == 5
