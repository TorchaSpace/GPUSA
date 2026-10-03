"""Pure warehouse logic shared by admin_app's Warehouses page and
depot_app's Console (no Qt, no SQL) - how full a warehouse is, what its
status reads, which employees work there, and how a stock movement is
described in a log.
"""

from __future__ import annotations

import math
import unicodedata
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from shared.formatting import to_db_timestamp
from shared.i18n import UserError, enum_label, tr
from shared.models import NEAR_CAPACITY_THRESHOLD, Warehouse

STATUS_NEAR = "Near capacity"
STATUS_OK = "Operational"
STATUS_UNSET = "Capacity not set"
STATUS_INACTIVE = "Inactive"


# --- input hygiene shared by the repositories and the forms ------------------

_MAX_WHOLE = 2**53  # far beyond any stock count; keeps values inside SQLite's 64-bit integers


def whole_number(value, what: str = "value") -> int:
    """`value` as an int, refusing anything that isn't already a whole
    number: 2.7 is an error (int() would silently turn it into 2), as are
    NaN/inf, booleans, blank text, "3.5" and absurdly large values.
    Accepts ints, whole floats (3.0) and digit strings. Raises ValueError
    naming `what`."""
    error = ValueError(f"{what} must be a whole number.")
    if isinstance(value, bool):
        raise error
    number: int | None = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            number = int(value)
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            pass
    else:
        try:
            if value == int(value):
                number = int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    if number is None or abs(number) > _MAX_WHOLE:
        raise error
    return number


def clean_price(value) -> float:
    """A price as a finite float >= 0, rounded half-up to 2 decimals
    (19.999 -> 20.0, 0.125 -> 0.13). Raises ValueError otherwise."""
    if isinstance(value, bool):
        raise UserError("err.price_number")
    try:
        number = Decimal(str(value).strip()) if isinstance(value, str) else Decimal(repr(float(value)))
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise UserError("err.price_number") from None
    if not number.is_finite():
        raise UserError("err.price_finite")
    if number < 0:
        raise UserError("err.price_negative")
    if number > Decimal(1_000_000_000):
        raise UserError("err.price_huge")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalise_barcode(barcode) -> str:
    """Barcodes are stored trimmed and upper-case so "abc-1" and "ABC-1"
    can't be two products. Raises ValueError if blank."""
    text = str(barcode or "").strip().upper()
    if not text:
        raise UserError("err.barcode_required")
    return text


def normalise_code(code, what: str = "code") -> str:
    """Warehouse/dealership codes: trimmed, upper-case. ValueError if blank."""
    text = str(code or "").strip().upper()
    if not text:
        raise UserError("err.code_required", what=enum_label("kind", what))
    return text


def tr_or(key: str, english: str) -> str:
    """tr(key), or `english` while the key isn't in the string tables yet
    (tr() itself falls back to the bare key, which reads badly)."""
    text = tr(key)
    return english if text == key else text


def location_label(location) -> str:
    """Display text for a StockLocation: its code, or "Unassigned" in the
    current language (StockLocation.label stays English - it is stored in
    movement references)."""
    return enum_label("location", "unassigned") if location.is_unassigned else location.code


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
    return STATUS_NEAR if percent_used(fraction) >= NEAR_CAPACITY_THRESHOLD * 100 else STATUS_OK


def percent_used(fraction: float) -> int:
    """Whole percent in use, FLOORED: 99.6% reads 99%, never a "100%" that
    looks full while the status still says Operational, and 84.9% never
    rounds up to the 85% that flips the status to Near capacity. (The 1e-9
    only absorbs float noise such as 0.29 * 100 = 28.999999999999996.)"""
    return int(math.floor(fraction * 100 + 1e-9))


def percent_text(fraction: float | None) -> str:
    return "—" if fraction is None else f"{percent_used(fraction)}%"


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
    return tr("wh.check_in") if movement["movement_type"] == "receive" else tr("wh.check_out")


def reason_label(movement: dict) -> str:
    key = movement.get("reason")
    return tr(f"wh.reason.{key}") if key in _REASON_LABELS else tr("wh.reason.floor")


def reference_text(movement: dict) -> str:
    """Reference column: the shipment number / other location, then the
    note, whichever exist."""
    parts = []
    reference = movement.get("reference")
    if reference:
        if movement.get("reason") == "transfer":
            parts.append(tr("wh.ref_from" if movement["movement_type"] == "receive" else "wh.ref_to").format(ref=reference))
        else:
            parts.append(reference)
    if movement.get("note"):
        parts.append(movement["note"])
    return " · ".join(parts) or "—"
