"""The administrative audit trail (`admin_audit`, migration v9): who deleted
a product, dealership or warehouse (and, later, who refunded a sale).

`record()` is called INSIDE the caller's transaction, so the row exists if
and only if the action did. The table is append-only (triggers).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from database.connection import connection_scope
from shared.auth import Actor, actor_label


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: str
    actor: str | None
    action: str
    kind: str
    target: str
    detail: str | None


def record(conn: sqlite3.Connection, actor: Actor | None, action: str, kind: str, target: str,
           detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO admin_audit (actor, action, kind, target, detail) VALUES (?, ?, ?, ?, ?)",
        (actor_label(actor), action, kind, target, detail),
    )


def list_recent(limit: int = 100, kind: str | None = None) -> list[AuditEntry]:
    sql = "SELECT id, at, actor, action, kind, target, detail FROM admin_audit"
    params: list = []
    if kind is not None:
        sql += " WHERE kind = ?"
        params.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with connection_scope() as conn:
        return [AuditEntry(**dict(row)) for row in conn.execute(sql, params).fetchall()]
