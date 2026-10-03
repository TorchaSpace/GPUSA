"""Pure warehouse logic shared by admin_app's Warehouses page and
depot_app's Console (no Qt, no SQL) - how full a warehouse is, what its
status reads, which employees work there, and how a stock movement is
described in a log.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, time

from shared.formatting import to_db_timestamp
from shared.models import NEAR_CAPACITY_THRESHOLD, Warehouse

STATUS_NEAR = "Near capacity"
STATUS_OK = "Operational"
STATUS_UNSET = "Capacity not set"
STATUS_INACTIVE = "Inactive"


def capacity_fraction(used_units: int, capacity_units: int | None) -> float | None:
    """Share of capacity in use (may exceed 1.0 when over-full), or None
    when no capacity is set."""
    if not capacity_units:
        return None
    return max(0, used_units) / capacity_units


def capacity_status(warehouse: Warehouse, used_units: int) -> str:
    """The card's status pill: Near capacity at/above the 85% threshold,
    else Operational; Capacity not set / Inactive where those apply."""
    if not warehouse.is_active:
        return STATUS_INACTIVE
    fraction = capacity_fraction(used_units, warehouse.capacity_units)
    if fraction is None:
        return STATUS_UNSET
    return STATUS_NEAR if fraction >= NEAR_CAPACITY_THRESHOLD else STATUS_OK


def percent_text(fraction: float | None) -> str:
    return "—" if fraction is None else f"{round(fraction * 100)}%"


def works_at(location_type: str, location_name: str, warehouse: Warehouse) -> bool:
    """Whether an employee record belongs to `warehouse`. Employees name
    their place as free text (employees.location_name), so a Warehouse
    employee matches on the code, the name, or the full "code · name"
    site label, ignoring case, accents and extra spaces."""
    if location_type != "Warehouse":
        return False
    wanted = _loose(location_name)
    return bool(wanted) and wanted in {_loose(warehouse.code), _loose(warehouse.name), _loose(warehouse.site_label)}


def _loose(text: str | None) -> str:
    """Case- and accent-insensitive form for matching typed names: Turkish
    "İstanbul" / "istanbul" / "ISTANBUL" all compare equal (plain
    casefold() leaves a combining dot on "İ", and "ı" isn't "i")."""
    decomposed = unicodedata.normalize("NFKD", (text or "").strip())
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.casefold().replace("ı", "i").split())


def start_of_today_db(today: date | None = None) -> str:
    """Local midnight today as a db (UTC) timestamp - "today" for the
    cards' inbound/outbound units."""
    return to_db_timestamp(datetime.combine(today or date.today(), time()))


_REASON_LABELS = {
    "receive": "Supplier receipt",
    "dispatch": "Written out",
    "shipment": "Shipment",
    "transfer": "Transfer",
    "count": "Stock count",
    "discrepancy": "Receipt discrepancy",
}


def direction_label(movement: dict) -> str:
    """The mockup's "Check-in" / "Check-out" movement column."""
    return "Check-in" if movement["movement_type"] == "receive" else "Check-out"


def reason_label(movement: dict) -> str:
    return _REASON_LABELS.get(movement.get("reason"), "Floor log")


def reference_text(movement: dict) -> str:
    """Reference column: the shipment number / other location, then the
    note, whichever exist."""
    parts = []
    reference = movement.get("reference")
    if reference:
        if movement.get("reason") == "transfer":
            parts.append(("from " if movement["movement_type"] == "receive" else "to ") + reference)
        else:
            parts.append(reference)
    if movement.get("note"):
        parts.append(movement["note"])
    return " · ".join(parts) or "—"
