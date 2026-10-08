"""All Dealership CRUD/queries. The only place SQL for `dealerships` is
allowed to live - mirrors product_repository.py's shape exactly (same
function names, same get-by-natural-key convention), with `code` playing
the role `barcode` plays there.

Scope note: this is deliberately a minimal v1. The Dealerships mockup
(Dealerships.dc.html) also shows revenue/7-day-trend figures, a per-
dealership staff roster, and a per-dealership stock-on-hand panel - all
three are fabricated client-side in the mockup's own script (sine waves
and hashed pseudo-random numbers), not derived from any real field it
renders, so there is nothing to persist for them. They're left for a
later slice. Stock on hand is real now - it's the dealership's own
stock levels (database/stock_repository.py), which is also why delete()
refuses a dealership that still has stock on its shelves. See
architecture.md.
"""

from __future__ import annotations

import sqlite3

from shared.auth import Actor
from shared.i18n import UserError
from database import audit_repository
from database.connection import connection_scope
from database.exceptions import DealershipNotFoundError, DuplicateDealershipCodeError, LocationHasStockError
from shared.models import DEALERSHIP_REGIONS, Dealership


MAX_CODE_LENGTH = 24
MAX_NAME_LENGTH = 120
MAX_CITY_LENGTH = 120
MAX_MANAGER_LENGTH = 120


def normalize_code(code: str | None) -> str:
    """A typed dealership code as stored: trimmed, upper-case ("cst-04 " is
    "CST-04"). Rows saved before this rule may be lower-case; lookups
    match those too (see _find_row)."""
    return (code or "").strip().upper()


def _find_row(conn, code: str) -> sqlite3.Row | None:
    raw = (code or "").strip()
    return conn.execute(
        "SELECT * FROM dealerships WHERE code = ? OR upper(code) = ? ORDER BY (code = ?) DESC, id LIMIT 1",
        (raw, raw.upper(), raw.upper()),
    ).fetchone()


def validate_fields(dealership: Dealership, *, require_code: bool = True) -> None:
    """Raise ValueError (message fit to show) for an empty or over-long
    code / name / city / manager. Pure - no database."""
    if require_code:
        code = normalize_code(dealership.code)
        if not code:
            raise UserError("err.dealership_code_required")
        if len(code) > MAX_CODE_LENGTH:
            raise UserError("err.code_too_long", n=MAX_CODE_LENGTH)
    name = (dealership.name or "").strip()
    if not name:
        raise UserError("err.dealership_name_required")
    if len(name) > MAX_NAME_LENGTH:
        raise UserError("err.name_too_long", n=MAX_NAME_LENGTH)
    if len((dealership.city or "").strip()) > MAX_CITY_LENGTH:
        raise UserError("err.city_too_long", n=MAX_CITY_LENGTH)
    if len((dealership.manager_name or "").strip()) > MAX_MANAGER_LENGTH:
        raise UserError("err.manager_too_long", n=MAX_MANAGER_LENGTH)


def _clean(dealership: Dealership) -> None:
    dealership.code = normalize_code(dealership.code)
    dealership.name = " ".join((dealership.name or "").split())
    dealership.city = " ".join((dealership.city or "").split())
    dealership.manager_name = " ".join((dealership.manager_name or "").split()) or None


def _validate_region(region: str) -> None:
    if region not in DEALERSHIP_REGIONS:
        raise ValueError(f"region must be one of {DEALERSHIP_REGIONS!r}, got {region!r}")


def _row_to_dealership(row: sqlite3.Row) -> Dealership:
    return Dealership(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        region=row["region"],
        city=row["city"],
        manager_name=row["manager_name"],
        is_active=bool(row["is_active"]),
    )


def get_by_code(code: str) -> Dealership:
    """Return the Dealership for `code` (any letter case, stray spaces
    ignored), or raise DealershipNotFoundError."""
    with connection_scope() as conn:
        row = _find_row(conn, code)
    if row is None:
        raise DealershipNotFoundError(normalize_code(code))
    return _row_to_dealership(row)


def list_all() -> list[Dealership]:
    """Return every dealership, e.g. for admin_app's Dealerships page."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM dealerships ORDER BY name").fetchall()
    return [_row_to_dealership(row) for row in rows]


def create(dealership: Dealership) -> None:
    """Insert a new dealership; its code is stored trimmed and upper-case.
    Raises DuplicateDealershipCodeError if the code exists (in any letter
    case), ValueError if region isn't one of DEALERSHIP_REGIONS or the
    code / name is empty or a field is too long."""
    _validate_region(dealership.region)
    _clean(dealership)
    validate_fields(dealership)
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _find_row(conn, dealership.code) is not None:
                raise DuplicateDealershipCodeError(dealership.code)
            try:
                conn.execute(
                    "INSERT INTO dealerships (code, name, region, city, manager_name, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        dealership.code,
                        dealership.name,
                        dealership.region,
                        dealership.city,
                        dealership.manager_name,
                        int(dealership.is_active),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateDealershipCodeError(dealership.code) from exc
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def update(dealership: Dealership) -> None:
    """Update an existing dealership's fields, keyed by its (fixed) code
    (any letter case). Raises ValueError if region isn't one of
    DEALERSHIP_REGIONS or a field is empty / too long,
    DealershipNotFoundError."""
    _validate_region(dealership.region)
    _clean(dealership)
    validate_fields(dealership)
    with connection_scope() as conn:
        row = _find_row(conn, dealership.code)
        if row is None:
            raise DealershipNotFoundError(dealership.code)
        conn.execute(
            "UPDATE dealerships SET name = ?, region = ?, city = ?, manager_name = ?, "
            "is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ?",
            (
                dealership.name,
                dealership.region,
                dealership.city,
                dealership.manager_name,
                int(dealership.is_active),
                row["id"],
            ),
        )


def delete(code: str, by: Actor | None = None) -> None:
    """Remove a dealership. Raises DealershipNotFoundError if it doesn't
    exist, LocationHasStockError if stock is still on its shelves (the
    units would be lost track of - move or count them out first),
    LocationInUseError while a scheduled / in-transit shipment is headed
    there (deactivate it instead)."""
    from database.exceptions import LocationInUseError  # local: keeps this edit clear of the file's imports
    from database.shipment_repository import active_count_for

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            held = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM stock_levels "
                "WHERE location_kind = 'dealership' AND location_code = ?",
                (code,),
            ).fetchone()[0]
            if held:
                raise LocationHasStockError(code, int(held))
            open_shipments = active_count_for(conn, dealership_code=code)
            if open_shipments:
                raise LocationInUseError("dealership", code, open_shipments)
            found = conn.execute("SELECT name FROM dealerships WHERE code = ?", (code,)).fetchone()
            name = found["name"] if found else None
            cursor = conn.execute("DELETE FROM dealerships WHERE code = ?", (code,))
            if cursor.rowcount == 0:
                raise DealershipNotFoundError(code)
            conn.execute("DELETE FROM stock_levels WHERE location_kind = 'dealership' AND location_code = ?", (code,))
            audit_repository.record(conn, by, "deleted", "dealership", code, name)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
