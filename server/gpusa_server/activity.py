"""The live activity feed (activity_events): what the tills and depots just did.

Written INSIDE the transaction of the action it describes, so an event exists
if and only if the action committed. Same shape as the desktop's
database/activity_repository.record()."""

from __future__ import annotations

import json

SEVERITIES = ("info", "notice", "warning", "critical")


def record(conn, kind: str, *, severity: str = "info", source: str = "system", place: tuple[str, str] | None = None,
           actor: str | None = None, **data) -> None:
    kind_, code = (place if place and place[0] != "unassigned" else (None, None))
    name = None
    if kind_ in ("warehouse", "dealership"):
        table = "warehouses" if kind_ == "warehouse" else "dealerships"
        row = conn.execute(f"SELECT name FROM {table} WHERE code = %s", (code,)).fetchone()
        name = row["name"] if row else None
    conn.execute(
        "INSERT INTO activity_events (kind, severity, source, location_kind, location_code, location_name, actor, "
        "data_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (kind, severity if severity in SEVERITIES else "info", source, kind_, code, name, actor,
         json.dumps(data, ensure_ascii=False, default=str)))


def reorder_level(conn, kind: str, code: str, barcode: str) -> int:
    """The level that applies at one place: Admin's own for it, else the product's."""
    row = conn.execute(
        "SELECT COALESCE(r.level, p.critical_stock_level) AS level FROM products p "
        "LEFT JOIN location_reorder_levels r ON r.product_barcode = p.barcode AND r.location_kind = %s "
        "AND r.location_code = %s WHERE p.barcode = %s", (kind, code, barcode)).fetchone()
    return int(row["level"]) if row else 0


def stock_crossing(conn, kind: str, code: str, barcode: str, before: int, after: int, *,
                   source: str | None = None, actor: str | None = None) -> None:
    """Low/out bell when stock LEAVES a real place and crosses its reorder level (or hits zero). Only the
    crossing counts, so a shelf that stays low does not repeat itself. Same rules as the desktop's
    activity_repository.stock_crossing; the unplaced pile never alerts."""
    if after >= before or kind == "unassigned":
        return
    row = conn.execute("SELECT name FROM products WHERE barcode = %s", (barcode,)).fetchone()
    if row is None:
        return
    source = source or ("depot" if kind == "warehouse" else "admin")
    place = (kind, code)
    if after <= 0 < before:
        record(conn, "stock_out", severity="critical", source=source, place=place, actor=actor,
               product=row["name"], barcode=barcode)
        return
    level = reorder_level(conn, kind, code, barcode)
    if level > 0 and before > level >= after:
        record(conn, "stock_low", severity="warning", source=source, place=place, actor=actor,
               product=row["name"], barcode=barcode, left=after, level=level)
