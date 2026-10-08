"""The live activity feed: what the tills and depots just did (`activity_events`).

record() is called INSIDE the transaction of the thing it describes (a sale,
a refund, a shipment step, a stock movement ...), so an event exists if and
only if the action committed. Nothing here ever raises into a business
action: an unexpected failure to write the feed is swallowed (the sale still
happens). The Admin window reads the feed with list_since() and renders each
event with shared/activity.py.

Severities, quiet to urgent: info (routine), notice (worth a look),
warning (needs attention), critical (act now). The bell counts notice and
above; the feed can show everything.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from database.connection import connection_scope
from shared.auth import Actor, actor_label
from shared.formatting import to_db_timestamp
from shared.models import ACTIVITY_SEVERITIES, ActivityEvent, StockLocation

RETENTION_DAYS = 30


def record(conn: sqlite3.Connection, kind: str, *, severity: str = "info", source: str = "system",
           location: StockLocation | None = None, location_name: str | None = None,
           actor: Actor | None = None, **data) -> None:
    """Append one event on the caller's connection (no commit of its own)."""
    if severity not in ACTIVITY_SEVERITIES:
        severity = "info"
    try:
        conn.execute(
            "INSERT INTO activity_events (kind, severity, source, location_kind, location_code, location_name, "
            "actor, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, severity, source,
             None if location is None or location.is_unassigned else location.kind,
             None if location is None or location.is_unassigned else location.code,
             location_name or _name_of(conn, location), actor_label(actor),
             json.dumps(data, ensure_ascii=False, default=str)),
        )
    except sqlite3.Error:  # the feed must never break a sale
        pass


def _name_of(conn: sqlite3.Connection, location: StockLocation | None) -> str | None:
    if location is None or location.is_unassigned:
        return None
    table = "dealerships" if location.kind == "dealership" else "warehouses"
    try:
        row = conn.execute(f"SELECT name FROM {table} WHERE code = ?", (location.code,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def stock_crossing(conn: sqlite3.Connection, location: StockLocation, barcode: str, before: int, after: int, *,
                   source: str, actor: Actor | None = None) -> None:
    """If stock at `location` just fell to/under the product's reorder level
    (or to zero), add a low/out event. Only the crossing counts, so a shelf
    that stays low does not repeat itself."""
    if after >= before:
        return
    row = conn.execute("SELECT name, critical_stock_level FROM products WHERE barcode = ?", (barcode,)).fetchone()
    if row is None:
        return
    level = int(row["critical_stock_level"])
    if after <= 0 < before:
        record(conn, "stock_out", severity="critical", source=source, location=location, actor=actor,
               product=row["name"], barcode=barcode)
    elif level > 0 and before > level >= after:
        record(conn, "stock_low", severity="warning", source=source, location=location, actor=actor,
               product=row["name"], barcode=barcode, left=after, level=level)


def _event(row: sqlite3.Row) -> ActivityEvent:
    from database.transaction_repository import _as_local, _parse_timestamp  # same timestamp rules as sales

    try:
        data = json.loads(row["data_json"] or "{}")
    except ValueError:
        data = {}
    return ActivityEvent(
        id=row["id"], at=_as_local(_parse_timestamp(row["at"])), kind=row["kind"], severity=row["severity"],
        source=row["source"], location_kind=row["location_kind"], location_code=row["location_code"],
        location_name=row["location_name"], actor=row["actor"], data=data if isinstance(data, dict) else {},
    )


def latest_id() -> int:
    with connection_scope() as conn:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM activity_events").fetchone()[0])


def list_since(after_id: int, limit: int = 200) -> list[ActivityEvent]:
    """Events with id > after_id, oldest first (what arrived since you last looked)."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM activity_events WHERE id > ? ORDER BY id LIMIT ?",
                            (int(after_id), int(limit))).fetchall()
    return [_event(r) for r in rows]


def list_recent(limit: int = 50, min_severity: str = "info", sources: tuple[str, ...] | None = None) -> list[ActivityEvent]:
    """Newest first, at least `min_severity`, optionally only from some sources."""
    allowed = ACTIVITY_SEVERITIES[ACTIVITY_SEVERITIES.index(min_severity):]
    sql = f"SELECT * FROM activity_events WHERE severity IN ({','.join('?' * len(allowed))})"
    params: list = list(allowed)
    if sources:
        sql += f" AND source IN ({','.join('?' * len(sources))})"
        params += list(sources)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with connection_scope() as conn:
        return [_event(r) for r in conn.execute(sql, params).fetchall()]


def count_since(after_id: int, min_severity: str = "notice") -> int:
    allowed = ACTIVITY_SEVERITIES[ACTIVITY_SEVERITIES.index(min_severity):]
    with connection_scope() as conn:
        return int(conn.execute(
            f"SELECT COUNT(*) FROM activity_events WHERE id > ? AND severity IN ({','.join('?' * len(allowed))})",
            [int(after_id), *allowed]).fetchone()[0])


def prune(days: int = RETENTION_DAYS) -> int:
    """Delete events older than `days`; returns how many went."""
    cutoff = to_db_timestamp(datetime.now(timezone.utc) - timedelta(days=days))
    with connection_scope() as conn:
        return conn.execute("DELETE FROM activity_events WHERE at < ?", (cutoff,)).rowcount
