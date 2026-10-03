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

from database.connection import connection_scope
from database.exceptions import DealershipNotFoundError, DuplicateDealershipCodeError, LocationHasStockError
from shared.models import DEALERSHIP_REGIONS, Dealership


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
    """Return the Dealership for `code`, or raise DealershipNotFoundError."""
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM dealerships WHERE code = ?", (code,)
        ).fetchone()
    if row is None:
        raise DealershipNotFoundError(code)
    return _row_to_dealership(row)


def list_all() -> list[Dealership]:
    """Return every dealership, e.g. for admin_app's Dealerships page."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM dealerships ORDER BY name").fetchall()
    return [_row_to_dealership(row) for row in rows]


def create(dealership: Dealership) -> None:
    """Insert a new dealership. Raises DuplicateDealershipCodeError if the
    code exists, ValueError if region isn't one of DEALERSHIP_REGIONS."""
    _validate_region(dealership.region)
    with connection_scope() as conn:
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


def update(dealership: Dealership) -> None:
    """Update an existing dealership's fields, keyed by its (fixed) code.
    Raises ValueError if region isn't one of DEALERSHIP_REGIONS."""
    _validate_region(dealership.region)
    with connection_scope() as conn:
        cursor = conn.execute(
            "UPDATE dealerships SET name = ?, region = ?, city = ?, manager_name = ?, "
            "is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE code = ?",
            (
                dealership.name,
                dealership.region,
                dealership.city,
                dealership.manager_name,
                int(dealership.is_active),
                dealership.code,
            ),
        )
    if cursor.rowcount == 0:
        raise DealershipNotFoundError(dealership.code)


def delete(code: str) -> None:
    """Remove a dealership. Raises DealershipNotFoundError if it doesn't
    exist, LocationHasStockError if stock is still on its shelves (the
    units would be lost track of - move or count them out first)."""
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
            cursor = conn.execute("DELETE FROM dealerships WHERE code = ?", (code,))
            if cursor.rowcount == 0:
                raise DealershipNotFoundError(code)
            conn.execute("DELETE FROM stock_levels WHERE location_kind = 'dealership' AND location_code = ?", (code,))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
