"""All Warehouse CRUD/queries. The only place SQL for `warehouses` is
allowed to live - same shape as dealership_repository.py, with `code`
("WH-01") as the natural key.

Each depot_app instance is one warehouse. Its row normally comes from
the setup wizard (a Depot_<n>.warehouse.json sidecar applied by
shared/warehouse_bootstrap.py); ensure_default() covers a depot started
without one, so the Floor always has a warehouse to put stock in.
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import DuplicateWarehouseCodeError, LocationHasStockError, WarehouseNotFoundError
from shared.models import Warehouse

DEFAULT_CODE = "WH-01"
DEFAULT_NAME = "Main warehouse"


def _row_to_warehouse(row: sqlite3.Row) -> Warehouse:
    return Warehouse(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        city=row["city"],
        capacity_units=row["capacity_units"],
        docks=row["docks"],
        is_active=bool(row["is_active"]),
    )


def _validated(warehouse: Warehouse) -> Warehouse:
    code, name = (warehouse.code or "").strip(), (warehouse.name or "").strip()
    if not code:
        raise ValueError("A warehouse needs a code.")
    if not name:
        raise ValueError("A warehouse needs a name.")
    capacity = warehouse.capacity_units
    if capacity is not None:
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError("Capacity must be above 0 (or left empty).")
    docks = int(warehouse.docks or 0)
    if docks < 0:
        raise ValueError("Docks can't be negative.")
    return Warehouse(
        code=code,
        name=name,
        city=(warehouse.city or "").strip(),
        capacity_units=capacity,
        docks=docks,
        is_active=bool(warehouse.is_active),
        id=warehouse.id,
    )


def get_by_code(code: str) -> Warehouse:
    with connection_scope() as conn:
        row = conn.execute("SELECT * FROM warehouses WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise WarehouseNotFoundError(code)
    return _row_to_warehouse(row)


def list_all(active_only: bool = False) -> list[Warehouse]:
    sql = "SELECT * FROM warehouses"
    if active_only:
        sql += " WHERE is_active = 1"
    with connection_scope() as conn:
        rows = conn.execute(sql + " ORDER BY code").fetchall()
    return [_row_to_warehouse(row) for row in rows]


def create(warehouse: Warehouse) -> Warehouse:
    """Insert a warehouse. Raises DuplicateWarehouseCodeError, ValueError."""
    w = _validated(warehouse)
    with connection_scope() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO warehouses (code, name, city, capacity_units, docks, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (w.code, w.name, w.city, w.capacity_units, w.docks, int(w.is_active)),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateWarehouseCodeError(w.code) from exc
        w.id = cursor.lastrowid
    return w


def update(warehouse: Warehouse) -> None:
    """Update a warehouse's details, keyed by its (fixed) code."""
    w = _validated(warehouse)
    with connection_scope() as conn:
        cursor = conn.execute(
            "UPDATE warehouses SET name = ?, city = ?, capacity_units = ?, docks = ?, is_active = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE code = ?",
            (w.name, w.city, w.capacity_units, w.docks, int(w.is_active), w.code),
        )
    if cursor.rowcount == 0:
        raise WarehouseNotFoundError(w.code)


def delete(code: str) -> None:
    """Remove a warehouse that holds no stock. Raises LocationHasStockError if any
    stock is still there (move it first - deleting would lose track of
    it), WarehouseNotFoundError if the code doesn't exist."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            held = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM stock_levels "
                "WHERE location_kind = 'warehouse' AND location_code = ?",
                (code,),
            ).fetchone()[0]
            if held:
                raise LocationHasStockError(code, int(held))
            cursor = conn.execute("DELETE FROM warehouses WHERE code = ?", (code,))
            if cursor.rowcount == 0:
                raise WarehouseNotFoundError(code)
            conn.execute(
                "DELETE FROM stock_levels WHERE location_kind = 'warehouse' AND location_code = ?", (code,)
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def ensure_default() -> Warehouse:
    """The warehouse a depot with no setup identity works as: the first
    active warehouse, or - on a database that has none at all - a new
    "WH-01 · Main warehouse" (capacity not set; editable in Admin)."""
    active = list_all(active_only=True)
    if active:
        return active[0]
    try:
        return get_by_code(DEFAULT_CODE)
    except WarehouseNotFoundError:
        pass
    try:
        return create(Warehouse(code=DEFAULT_CODE, name=DEFAULT_NAME))
    except DuplicateWarehouseCodeError:  # another depot created it a moment ago
        return get_by_code(DEFAULT_CODE)
