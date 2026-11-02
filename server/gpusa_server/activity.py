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
