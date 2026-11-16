from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import connect


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table if it is missing (lightweight migration)."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate(conn) -> None:
    _ensure_column(conn, "price_tickets", "effective_until", "effective_until TEXT")
    _ensure_column(conn, "price_tickets", "reverted_at", "reverted_at TEXT")
    _ensure_column(conn, "price_tickets", "revert_batch_id", "revert_batch_id INTEGER")
    _ensure_column(conn, "price_tickets", "voided_at", "voided_at TEXT")
    _ensure_column(conn, "apply_batches", "kind", "kind TEXT NOT NULL DEFAULT 'apply'")
    _ensure_column(conn, "price_book", "source_kind", "source_kind TEXT NOT NULL DEFAULT 'apply'")
    _ensure_column(conn, "price_history", "source_kind", "source_kind TEXT NOT NULL DEFAULT 'apply'")
    # legacy rows default to 'apply' via ALTER; rows without a source ticket are seeded list-price rows
    conn.execute("UPDATE price_book SET source_kind='seed' WHERE source_ticket_no IS NULL AND source_kind='apply'")
    conn.execute("UPDATE price_history SET source_kind='seed' WHERE source_ticket_no IS NULL AND source_kind='apply'")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_schema(conn) -> None:
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skus (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          category TEXT NOT NULL,
          list_price REAL NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS price_tickets (
          id INTEGER PRIMARY KEY,
          ticket_no TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          status TEXT NOT NULL,
          effective_at TEXT NOT NULL,
          effective_until TEXT,
          note TEXT,
          created_at TEXT NOT NULL,
          applied_at TEXT,
          apply_batch_id INTEGER,
          reverted_at TEXT,
          revert_batch_id INTEGER,
          voided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS price_ticket_lines (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER NOT NULL REFERENCES price_tickets(id) ON DELETE CASCADE,
          sku_code TEXT NOT NULL,
          old_price REAL NOT NULL,
          new_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS apply_batches (
          id INTEGER PRIMARY KEY,
          batch_no TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL DEFAULT 'apply',
          clock_at TEXT NOT NULL,
          ticket_count INTEGER NOT NULL,
          line_count INTEGER NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_book (
          sku_code TEXT PRIMARY KEY,
          current_price REAL NOT NULL,
          source_ticket_no TEXT,
          source_kind TEXT NOT NULL DEFAULT 'seed',
          effective_at TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY,
          sku_code TEXT NOT NULL,
          price REAL NOT NULL,
          source_ticket_no TEXT,
          source_kind TEXT NOT NULL DEFAULT 'seed',
          effective_at TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sales_fixture (
          id INTEGER PRIMARY KEY,
          sale_no TEXT NOT NULL UNIQUE,
          sku_code TEXT NOT NULL,
          sold_at TEXT NOT NULL,
          unit_price REAL NOT NULL,
          qty INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hold_applies (
          id INTEGER PRIMARY KEY,
          ticket_id INTEGER,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        '''
    )


def seed_if_empty() -> None:
    conn = connect()
    try:
        init_schema(conn)
        _migrate(conn)
        n = conn.execute("SELECT COUNT(*) c FROM skus").fetchone()["c"]
        if n:
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES ('revert_strategy', 'list_price')"
            )
            conn.commit()
            return

        skus = json.loads((FIXTURES / "skus.json").read_text(encoding="utf-8"))
        sales = json.loads((FIXTURES / "sales.json").read_text(encoding="utf-8"))
        now = _now()
        for s in skus:
            conn.execute(
                "INSERT INTO skus(sku_code, title, category, list_price, enabled) VALUES (?,?,?,?,1)",
                (s["sku_code"], s["title"], s["category"], float(s["list_price"])),
            )
            conn.execute(
                "INSERT INTO price_book(sku_code, current_price, source_ticket_no, source_kind, effective_at, updated_at) VALUES (?,?,NULL,'seed',?,?)",
                (s["sku_code"], float(s["list_price"]), now, now),
            )
            conn.execute(
                "INSERT INTO price_history(sku_code, price, source_ticket_no, source_kind, effective_at, recorded_at) VALUES (?,?,NULL,'seed',?,?)",
                (s["sku_code"], float(s["list_price"]), now, now),
            )
        for row in sales:
            conn.execute(
                "INSERT INTO sales_fixture(sale_no, sku_code, sold_at, unit_price, qty) VALUES (?,?,?,?,?)",
                (row["sale_no"], row["sku_code"], row["sold_at"], float(row["unit_price"]), int(row["qty"])),
            )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('business_clock', ?)",
            ("2026-11-16T09:00:00Z",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_label', ?)",
            ("业务钟（可拨，用于判断改价是否到期）",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES ('revert_strategy', ?)",
            ("list_price",),
        )
        conn.commit()
    finally:
        conn.close()
