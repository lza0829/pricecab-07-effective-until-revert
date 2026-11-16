from __future__ import annotations

import sqlite3
from typing import Any


BATCH_PREFIX = {"apply": "AB", "revert": "RB"}


def next_batch_no(conn: sqlite3.Connection, kind: str = "apply") -> str:
    prefix = BATCH_PREFIX.get(kind, "AB")
    n = conn.execute(
        "SELECT COUNT(*) c FROM apply_batches WHERE kind=?", (kind,)
    ).fetchone()["c"] + 1
    return f"{prefix}-{n:04d}"


def create_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    kind: str,
    clock_at: str,
    ticket_count: int,
    line_count: int,
    status: str,
    created_at: str,
) -> int:
    cur = conn.execute(
        '''
        INSERT INTO apply_batches(batch_no, kind, clock_at, ticket_count, line_count, status, created_at)
        VALUES (?,?,?,?,?,?,?)
        ''',
        (batch_no, kind, clock_at, ticket_count, line_count, status, created_at),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM apply_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
