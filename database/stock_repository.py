"""Per-location stock: the only place SQL for `stock_levels` and for
writing `stock_movements` lives.

Every unit of stock sits at one place - a warehouse, a dealership, or
UNASSIGNED (not placed yet; see database/migrations.py) - or is on a
truck (a dispatched shipment, see shipment_repository.py). The
company-wide `products.stock_quantity` is kept as the total of all of
that, in the same transaction as every change here:

    products.stock_quantity = SUM(stock_levels) + units on dispatched,
                              not-yet-received shipments

So there are two kinds of change:
- stock entering or leaving the company (depot receives from a
  supplier, dispatches damaged goods, a POS sale, a stock count
  correction, a receipt discrepancy) moves a level AND the total;
- stock moving inside the company (a transfer between places, loading a
  shipment) moves levels only.

The conn-level helpers (change_level, log_movement, require_location)
take an open connection so transaction_repository.py and
shipment_repository.py can use them inside their own BEGIN IMMEDIATE
transactions; the public functions open their own.
"""

from __future__ import annotations

import sqlite3

from shared.i18n import UserError
from database.connection import connection_scope
from database.exceptions import (
    CapacityExceededError,
    InsufficientStockError,
    LocationInactiveError,
    ProductInactiveError,
    ProductNotFoundError,
    UnknownLocationError,
)
from shared.auth import Actor, actor_label
from shared.models import UNASSIGNED, Product, StockLevel, StockLocation
from shared.warehousing import whole_number

_TABLE_FOR_KIND = {"warehouse": "warehouses", "dealership": "dealerships"}


# --- helpers that run inside a caller's transaction -------------------------

def require_location(conn: sqlite3.Connection, location: StockLocation) -> None:
    """Raise UnknownLocationError unless `location` is UNASSIGNED or names
    an existing warehouse/dealership."""
    table = _TABLE_FOR_KIND.get(location.kind)
    if table is None:
        return
    if conn.execute(f"SELECT 1 FROM {table} WHERE code = ?", (location.code,)).fetchone() is None:
        raise UnknownLocationError(location.kind, location.code)


def resolve_product(conn: sqlite3.Connection, barcode: str, active_only: bool = False) -> tuple[str, str]:
    """(stored barcode, name) for `barcode` in any letter case. Raises
    ProductNotFoundError, or - with `active_only` - ProductInactiveError for
    a deactivated product (stock coming IN or going on a shipment must not
    use one; stock going out may)."""
    text = str(barcode).strip()
    row = conn.execute(
        "SELECT barcode, name, is_active FROM products WHERE barcode = ? COLLATE NOCASE ORDER BY barcode = ? DESC",
        (text, text),
    ).fetchone()
    if row is None:
        raise ProductNotFoundError(barcode)
    if active_only and not row["is_active"]:
        raise ProductInactiveError(row["barcode"])
    return row["barcode"], row["name"]


def require_product(conn: sqlite3.Connection, barcode: str, active_only: bool = False) -> str:
    """Return the product's name, or raise ProductNotFoundError."""
    return resolve_product(conn, barcode, active_only)[1]


def require_can_hold(conn: sqlite3.Connection, location: StockLocation, units: int) -> None:
    """Refuse inbound stock a warehouse can't take: LocationInactiveError
    for a deactivated one, CapacityExceededError if `units` more would go
    past its capacity (no capacity set = no limit). Other places always
    pass. Stock returning to its own origin (a cancelled shipment) and
    count corrections deliberately skip this: the units exist."""
    if location.kind != "warehouse":
        return
    row = conn.execute(
        "SELECT capacity_units, is_active FROM warehouses WHERE code = ?", (location.code,)
    ).fetchone()
    if row is None:
        raise UnknownLocationError(location.kind, location.code)
    if not row["is_active"]:
        raise LocationInactiveError(location.code)
    capacity = row["capacity_units"]
    if capacity is not None:
        used = int(conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM stock_levels WHERE location_kind = 'warehouse' "
            "AND location_code = ?", (location.code,)
        ).fetchone()[0])
        if used + int(units) > capacity:
            raise CapacityExceededError(location.code, int(capacity), used, int(units))


def level_in(conn: sqlite3.Connection, location: StockLocation, barcode: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM stock_levels WHERE location_kind = ? AND location_code = ? AND product_barcode = ?",
        (location.kind, location.code, barcode),
    ).fetchone()
    return int(row["quantity"]) if row else 0


def change_level(
    conn: sqlite3.Connection, location: StockLocation, barcode: str, delta: int, *, change_total: bool
) -> int:
    """Add `delta` (may be negative) to one level and return the new level.
    Raises InsufficientStockError - before writing - if it would go below
    zero. With change_total, products.stock_quantity moves by the same
    delta (stock entering/leaving the company); without, it doesn't
    (stock moving between places)."""
    current = level_in(conn, location, barcode)
    new = current + int(delta)
    if new < 0:
        raise InsufficientStockError(barcode, -int(delta), current, location.label)
    conn.execute(
        "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (location_kind, location_code, product_barcode) DO UPDATE SET "
        "quantity = excluded.quantity, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        (location.kind, location.code, barcode, new),
    )
    if change_total and delta:
        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity + ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE barcode = ?",
            (int(delta), barcode),
        )
    return new


def log_movement(
    conn: sqlite3.Connection,
    location: StockLocation,
    barcode: str,
    movement_type: str,
    quantity: int,
    *,
    reason: str,
    note: str | None = None,
    reference: str | None = None,
    actor: Actor | None = None,
    bin_code: str | None = None,
) -> None:
    """One `stock_movements` audit row. movement_type is 'receive' (units
    arrived at `location`) or 'dispatch' (units left it); `reason` and
    `reference` are documented in schema.sql; `actor` is who was signed in
    (None on the open depot Floor)."""
    conn.execute(
        "INSERT INTO stock_movements (product_barcode, movement_type, quantity, note, "
        "location_kind, location_code, reason, reference, handled_by, bin_code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (barcode, movement_type, int(quantity), note, location.kind, location.code, reason, reference,
         actor_label(actor), _clean(bin_code)),
    )


def receive_in(conn: sqlite3.Connection, location: StockLocation, barcode: str, quantity: int, *,
               note: str | None = None, actor: Actor | None = None, reference: str | None = None,
               bin_code: str | None = None) -> int:
    """receive() inside the caller's open transaction (it neither begins nor
    commits one): the checks, the level + company-total change and the
    'receive' audit row, so purchase_order_repository can book a delivery
    against an order atomically with the order's own update. `quantity`
    must already be a positive whole number. `reference` (e.g. "PO-00042")
    is stored on the movement. Returns the new level there."""
    require_location(conn, location)
    code = resolve_product(conn, barcode, active_only=True)[0]
    require_can_hold(conn, location, quantity)
    new = change_level(conn, location, code, quantity, change_total=True)
    log_movement(conn, location, code, "receive", quantity, reason="receive", note=_clean(note),
                 reference=reference, actor=actor, bin_code=bin_code)
    return new


def _write(fn):
    """Run fn(conn) inside one BEGIN IMMEDIATE transaction."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(conn)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return result


def _positive(quantity: int) -> int:
    quantity = whole_number(quantity, "Quantity")
    if quantity <= 0:
        raise ValueError("quantity must be greater than 0")
    return quantity


def _clean(note: str | None) -> str | None:
    return (note or "").strip() or None


# --- writes -----------------------------------------------------------------

def receive(location: StockLocation, barcode: str, quantity: int, note: str | None = None,
            actor: Actor | None = None, reference: str | None = None, bin_code: str | None = None) -> int:
    """New stock arrives at `location` from outside the company (the depot
    Floor's Inbound). Returns the new level there."""
    quantity = _positive(quantity)
    return _write(lambda conn: receive_in(conn, location, barcode, quantity, note=note, actor=actor,
                                          reference=_clean(reference), bin_code=bin_code))


def dispatch(location: StockLocation, barcode: str, quantity: int, note: str | None = None,
             actor: Actor | None = None, reference: str | None = None, bin_code: str | None = None) -> int:
    """Stock leaves the company from `location` (damaged, returned to the
    supplier, scrapped - the depot Floor's Outbound; NOT a shipment to a
    dealership, which stays inside the company). Raises
    InsufficientStockError if `location` has fewer than `quantity`."""
    quantity = _positive(quantity)

    def run(conn):
        require_location(conn, location)
        code = resolve_product(conn, barcode)[0]
        new = change_level(conn, location, code, -quantity, change_total=True)
        log_movement(conn, location, code, "dispatch", quantity, reason="dispatch", note=_clean(note),
                     reference=_clean(reference), actor=actor, bin_code=bin_code)
        return new

    return _write(run)


def transfer(
    source: StockLocation, destination: StockLocation, barcode: str, quantity: int, note: str | None = None,
    actor: Actor | None = None,
) -> None:
    """Move stock between two places at once (e.g. placing unassigned stock
    into a warehouse, or rebalancing two warehouses). The company total
    doesn't change. For goods that travel by truck, use a shipment."""
    quantity = _positive(quantity)
    if source == destination:
        raise UserError("err.two_locations")

    def run(conn):
        require_location(conn, source)
        require_location(conn, destination)
        code = resolve_product(conn, barcode)[0]
        require_can_hold(conn, destination, quantity)
        change_level(conn, source, code, -quantity, change_total=False)
        change_level(conn, destination, code, quantity, change_total=False)
        note_text = _clean(note)
        log_movement(conn, source, code, "dispatch", quantity, reason="transfer", note=note_text,
                     reference=destination.label, actor=actor)
        log_movement(conn, destination, code, "receive", quantity, reason="transfer", note=note_text,
                     reference=source.label, actor=actor)

    _write(run)


def set_count(location: StockLocation, barcode: str, counted: int, note: str | None = None,
              actor: Actor | None = None) -> int:
    """A stock count: someone counted `counted` units at `location`. The
    level is set to that and the difference is written as a 'count'
    movement (and moves the company total - the units were found or
    lost). Returns the difference (counted - previous level)."""
    counted = whole_number(counted, "Count")
    if counted < 0:
        raise UserError("err.count_negative")

    def run(conn):
        require_location(conn, location)
        code = resolve_product(conn, barcode)[0]
        diff = counted - level_in(conn, location, code)
        if diff:
            change_level(conn, location, code, diff, change_total=True)
            log_movement(conn, location, code, "receive" if diff > 0 else "dispatch", abs(diff),
                         reason="count", note=_clean(note) or f"Counted {counted}", actor=actor)
        return diff

    return _write(run)


def place_all_unassigned(destination: StockLocation, actor: Actor | None = None) -> int:
    """Move every unassigned unit, of every product, to `destination` (the
    one-click "these are all in the main warehouse" after upgrading).
    Returns the number of units moved."""
    if destination.is_unassigned:
        raise UserError("err.pick_location")

    def run(conn):
        require_location(conn, destination)
        rows = conn.execute(
            "SELECT product_barcode, quantity FROM stock_levels "
            "WHERE location_kind = 'unassigned' AND quantity > 0"
        ).fetchall()
        require_can_hold(conn, destination, sum(int(r["quantity"]) for r in rows))
        moved = 0
        for row in rows:
            qty = int(row["quantity"])
            change_level(conn, UNASSIGNED, row["product_barcode"], -qty, change_total=False)
            change_level(conn, destination, row["product_barcode"], qty, change_total=False)
            log_movement(conn, UNASSIGNED, row["product_barcode"], "dispatch", qty, reason="transfer",
                         reference=destination.label, note="Placed unassigned stock", actor=actor)
            log_movement(conn, destination, row["product_barcode"], "receive", qty, reason="transfer",
                         reference=UNASSIGNED.label, note="Placed unassigned stock", actor=actor)
            moved += qty
        return moved

    return _write(run)


# --- reads ------------------------------------------------------------------

def _location_of(kind: str | None, code: str | None) -> StockLocation | None:
    if kind is None:
        return None  # a movement logged before per-location stock existed
    return StockLocation(kind, code or "")


def quantity_at(location: StockLocation, barcode: str) -> int:
    with connection_scope() as conn:
        return level_in(conn, location, barcode)


_PRODUCT_AT_SQL = (
    "SELECT p.barcode, p.name, p.price, p.critical_stock_level, p.is_active, COALESCE(l.quantity, 0) AS qty, "
    "(l.product_barcode IS NOT NULL) AS stocked "
    "FROM products p LEFT JOIN stock_levels l ON l.product_barcode = p.barcode "
    "AND l.location_kind = ? AND l.location_code = ? "
)


def _product_here(r: sqlite3.Row) -> Product:
    return Product(barcode=r["barcode"], name=r["name"], price=r["price"], stock_quantity=r["qty"],
                   critical_stock_level=r["critical_stock_level"], is_active=bool(r["is_active"]),
                   stocked_here=bool(r["stocked"]))


def products_at(location: StockLocation, include_inactive: bool = False) -> list[Product]:
    """Every product, with `stock_quantity` set to what's at `location`
    (0 where nothing is) - what POS and the depot show as "my stock".
    `critical_stock_level` is the product's own (one threshold applies
    at every location); `stocked_here` says whether this location has (or
    had) a level row for it.

    Deactivated products are left out - except those still holding units
    here, so physical stock never disappears from the list (callers that
    SELL or RECEIVE must still skip `not p.is_active`). `include_inactive`
    returns all of them."""
    sql = _PRODUCT_AT_SQL
    if not include_inactive:
        sql += "WHERE p.is_active = 1 OR COALESCE(l.quantity, 0) > 0 "
    with connection_scope() as conn:
        rows = conn.execute(sql + "ORDER BY p.name", (location.kind, location.code)).fetchall()
    return [_product_here(r) for r in rows]


def product_at(location: StockLocation, barcode: str, active_only: bool = False) -> Product:
    """One product (barcode in any letter case) with its local quantity.
    Raises ProductNotFoundError; with `active_only` a deactivated product
    raises ProductInactiveError - the POS sale lookup."""
    text = str(barcode).strip()
    with connection_scope() as conn:
        row = conn.execute(
            _PRODUCT_AT_SQL + "WHERE p.barcode = ? COLLATE NOCASE ORDER BY p.barcode = ? DESC",
            (location.kind, location.code, text, text),
        ).fetchone()
    if row is None:
        raise ProductNotFoundError(barcode)
    if active_only and not row["is_active"]:
        raise ProductInactiveError(row["barcode"])
    return _product_here(row)


def critical_at(location: StockLocation) -> list[Product]:
    """Products at/below their reorder level AT `location` - only active
    products this location actually has a stock level row for (it has
    stocked them before). A shop that never carried a product is not
    alerted about it, and a product with no reorder level set (0) is never
    listed. The network-wide equivalent is
    product_repository.get_critical_stock_list()."""
    return [p for p in products_at(location) if p.is_active and p.stocked_here and p.is_below_critical_stock]


def levels_for_product(barcode: str) -> list[StockLevel]:
    """Where a product is: every location holding any of it, most first."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT l.location_kind, l.location_code, l.product_barcode, p.name, l.quantity "
            "FROM stock_levels l JOIN products p ON p.barcode = l.product_barcode "
            "WHERE l.product_barcode = ? AND l.quantity > 0 ORDER BY l.quantity DESC, l.location_code",
            (barcode,),
        ).fetchall()
    return [StockLevel(StockLocation(r[0], r[1]), r[2], r[3], r[4]) for r in rows]


def levels_at(location: StockLocation) -> list[StockLevel]:
    """Every product held at `location` (quantity > 0), by name."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT l.location_kind, l.location_code, l.product_barcode, p.name, l.quantity "
            "FROM stock_levels l JOIN products p ON p.barcode = l.product_barcode "
            "WHERE l.location_kind = ? AND l.location_code = ? AND l.quantity > 0 ORDER BY p.name",
            (location.kind, location.code),
        ).fetchall()
    return [StockLevel(StockLocation(r[0], r[1]), r[2], r[3], r[4]) for r in rows]


def all_levels() -> list[StockLevel]:
    """Every non-zero stock level, by product name then location - the
    Admin "Stock by location" grid."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT l.location_kind, l.location_code, l.product_barcode, p.name, l.quantity "
            "FROM stock_levels l JOIN products p ON p.barcode = l.product_barcode "
            "WHERE l.quantity > 0 ORDER BY p.name, l.location_kind, l.location_code"
        ).fetchall()
    return [StockLevel(StockLocation(r[0], r[1]), r[2], r[3], r[4]) for r in rows]


def units_by_location() -> dict[StockLocation, int]:
    """Total units held at each location that holds any - a warehouse's
    "used" capacity is its entry here."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT location_kind, location_code, SUM(quantity) FROM stock_levels "
            "GROUP BY location_kind, location_code HAVING SUM(quantity) > 0"
        ).fetchall()
    return {StockLocation(r[0], r[1]): int(r[2]) for r in rows}


def units_in_transit() -> int:
    """Units on dispatched shipments not yet received."""
    with connection_scope() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(l.expected_qty), 0) FROM shipment_lines l JOIN shipments s ON s.id = l.shipment_id "
            "WHERE s.status = 'in_transit' AND s.stock_moved = 1"
        ).fetchone()[0])


def list_movements(
    limit: int | None = 50,
    location: StockLocation | None = None,
    movement_type: str | None = None,
    location_kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Recent stock movements, newest first, as plain dicts: id, barcode,
    product_name, movement_type, quantity, note, created_at, location
    (a StockLocation, or None for rows logged before per-location stock),
    reason, reference, handled_by, bin_code. Filter by one location, by a kind of
    location ('warehouse' - Admin's Movement Logs), by direction, and/or
    by time - `since` (inclusive) / `until` (exclusive) db timestamps, for
    the depot's Reports. `limit` None = no limit."""
    if movement_type is not None and movement_type not in ("receive", "dispatch"):
        raise ValueError("movement_type must be 'receive', 'dispatch', or None")
    clauses, params = [], []
    if location is not None:
        clauses.append("m.location_kind = ? AND m.location_code = ?")
        params += [location.kind, location.code]
    if location_kind is not None:
        clauses.append("m.location_kind = ?")
        params.append(location_kind)
    if movement_type is not None:
        clauses.append("m.movement_type = ?")
        params.append(movement_type)
    if since is not None:
        clauses.append("m.created_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("m.created_at < ?")
        params.append(until)
    sql = (
        "SELECT m.id, m.product_barcode, p.name AS product_name, m.movement_type, m.quantity, m.note, "
        "m.created_at, m.location_kind, m.location_code, m.reason, m.reference, m.handled_by, m.bin_code "
        "FROM stock_movements m JOIN products p ON p.barcode = m.product_barcode"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY m.created_at DESC, m.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r["id"],
            "barcode": r["product_barcode"],
            "product_name": r["product_name"],
            "movement_type": r["movement_type"],
            "quantity": r["quantity"],
            "note": r["note"],
            "created_at": r["created_at"],
            "location": _location_of(r["location_kind"], r["location_code"]),
            "reason": r["reason"],
            "reference": r["reference"],
            "handled_by": r["handled_by"],
            "bin_code": r["bin_code"],
        }
        for r in rows
    ]


def movement_totals_since(since_db_timestamp: str) -> dict[StockLocation, tuple[int, int]]:
    """Units in and out per location since a UTC db timestamp (the
    Warehouses cards' "inbound / outbound today")."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT location_kind, location_code, "
            "SUM(CASE WHEN movement_type = 'receive' THEN quantity ELSE 0 END), "
            "SUM(CASE WHEN movement_type = 'dispatch' THEN quantity ELSE 0 END) "
            "FROM stock_movements WHERE location_kind IS NOT NULL AND created_at >= ? "
            "GROUP BY location_kind, location_code",
            (since_db_timestamp,),
        ).fetchall()
    return {StockLocation(r[0], r[1]): (int(r[2]), int(r[3])) for r in rows}


def last_movement_at(location: StockLocation) -> dict[str, str]:
    """barcode -> created_at of the latest movement at `location` (the
    Console Inventory's "Last moved" column)."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT product_barcode, MAX(created_at) FROM stock_movements "
            "WHERE location_kind = ? AND location_code = ? GROUP BY product_barcode",
            (location.kind, location.code),
        ).fetchall()
    return {r[0]: r[1] for r in rows}
