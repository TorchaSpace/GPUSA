"""All Product CRUD/queries. The only place SQL for `products` is allowed to live.

`pos_app`, `depot_app`, and `admin_app` call these functions - they
never write SQL of their own, and never import each other to reach this
indirectly. Every function returns/accepts `shared.models.Product`
instances, not raw sqlite3.Row objects, so callers never need to know
the storage is SQLite.

Rules enforced here (not just in the form):
- barcodes are stored trimmed + upper-case and looked up case-insensitively;
- names are non-blank, prices finite and >= 0 rounded to 2 decimals,
  reorder levels whole numbers >= 0;
- a product with stock or any history (stock movements, sales, shipment
  lines) can't be deleted - deactivate it (`set_active`) instead;
- deactivated products stay in `list_all()` (Admin shows them dimmed) but
  not in `list_active()` and not in the sale / movement lookups that ask
  for `active_only`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from shared.i18n import UserError
from database.connection import connection_scope
from database.exceptions import (
    DuplicateBarcodeError,
    ProductInactiveError,
    ProductInUseError,
    ProductNotFoundError,
)
from database.stock_repository import level_in, log_movement
from shared.costing import clean_cost
from shared.currency import format_money
from shared.models import UNASSIGNED, Product, StockLocation
from shared.warehousing import clean_price, normalise_barcode, whole_number


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        barcode=row["barcode"],
        name=row["name"],
        price=row["price"],
        stock_quantity=row["stock_quantity"],
        critical_stock_level=row["critical_stock_level"],
        is_active=bool(row["is_active"]),
        cost_price=row["cost_price"],
    )


def _validated(product: Product) -> Product:
    """A cleaned copy of `product` (stock_quantity validated, not changed),
    or ValueError saying what's wrong."""
    barcode = normalise_barcode(product.barcode)
    name = (product.name or "").strip()
    if not name:
        raise UserError("err.product_name_required")
    price = clean_price(product.price)
    critical = whole_number(product.critical_stock_level, "Critical level")
    if critical < 0:
        raise UserError("err.critical_negative")
    stock = whole_number(product.stock_quantity, "Stock quantity")
    if stock < 0:
        raise UserError("err.stock_negative")
    return Product(barcode=barcode, name=name, price=price, stock_quantity=stock, critical_stock_level=critical,
                   is_active=bool(product.is_active), cost_price=clean_cost(product.cost_price))


def _find(conn: sqlite3.Connection, barcode: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM products WHERE barcode = ? COLLATE NOCASE ORDER BY barcode = ? DESC",
        (str(barcode).strip(), str(barcode).strip()),
    ).fetchone()


def get_by_barcode(barcode: str, active_only: bool = False) -> Product:
    """Return the Product for `barcode` (any letter case), or raise
    ProductNotFoundError. With `active_only`, a deactivated product raises
    ProductInactiveError (a ProductNotFoundError) - what a sale lookup uses."""
    with connection_scope() as conn:
        row = _find(conn, barcode)
    if row is None:
        raise ProductNotFoundError(barcode)
    if active_only and not row["is_active"]:
        raise ProductInactiveError(row["barcode"])
    return _row_to_product(row)


def list_all(include_inactive: bool = True) -> list[Product]:
    """Return every product, e.g. for the Admin product management tab
    (deactivated ones included - Admin shows them dimmed - unless
    `include_inactive` is False)."""
    sql = "SELECT * FROM products"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    with connection_scope() as conn:
        rows = conn.execute(sql + " ORDER BY name").fetchall()
    return [_row_to_product(row) for row in rows]


def list_active() -> list[Product]:
    """Products that can be sold / received / shipped."""
    return list_all(include_inactive=False)


def get_critical_stock_list() -> list[Product]:
    """Active products whose NETWORK-WIDE total (every warehouse, dealership
    and truck added together) is at or below their reorder level.

    A reorder level of 0 means none is set, so such a product is not
    listed (a new product with no stock is not "critical"). This is the
    company-wide view; a single location's own alerts come from
    stock_repository.critical_at(). See shared/gui_kit/polling.py for how
    depot_app re-runs per-location alerts on an interval, and
    admin_app/gui/inventory_health_tab.py for the passive equivalent.
    """
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE is_active = 1 AND critical_stock_level > 0 "
            "AND stock_quantity <= critical_stock_level ORDER BY name"
        ).fetchall()
    return [_row_to_product(row) for row in rows]


def create(product: Product) -> None:
    """Insert a new product. Raises ValueError for a blank name/barcode, a
    bad price / level / quantity, and DuplicateBarcodeError ONLY when the
    barcode (any letter case) really exists already.

    Any starting `stock_quantity` is recorded as UNASSIGNED stock (the
    product form doesn't ask where it is) together with a 'receive'
    stock_movements row, so the stock has an audit trail from day one;
    Admin > Warehouses moves it to a place. One transaction, so the total,
    the level and the audit row never disagree.
    """
    p = _validated(product)
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _find(conn, p.barcode) is not None:
                raise DuplicateBarcodeError(p.barcode)
            try:
                conn.execute(
                    "INSERT INTO products "
                    "(barcode, name, price, stock_quantity, critical_stock_level, is_active, cost_price) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (p.barcode, p.name, p.price, p.stock_quantity, p.critical_stock_level, int(p.is_active),
                     p.cost_price),
                )
            except sqlite3.IntegrityError as exc:
                if _find(conn, p.barcode) is not None:  # lost a race with another writer
                    raise DuplicateBarcodeError(p.barcode) from exc
                raise ValueError(f"The database rejected this product: {exc}") from exc
            if p.stock_quantity:
                conn.execute(
                    "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
                    "VALUES ('unassigned', '', ?, ?)",
                    (p.barcode, p.stock_quantity),
                )
                log_movement(conn, UNASSIGNED, p.barcode, "receive", p.stock_quantity, reason="receive",
                             note="Initial stock entered when the product was created")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def update(product: Product) -> None:
    """Update an existing product's fields (name/price/critical_stock_level),
    with the same validation as create().

    Stock quantity is intentionally NOT updated here - it's the company
    total of the per-location levels, and only ever changes through
    database/stock_repository.py (receipts, dispatches, counts,
    transfers), a sale (transaction_repository) or a shipment
    (shipment_repository), each with an audit row - never a silent edit.
    Active/inactive is changed with set_active(), not here. The cost is not
    touched either (it moves with purchase-order receipts, so a form open
    for a minute must not write back a stale one): use set_cost().
    """
    p = _validated(product)
    with connection_scope() as conn:
        row = _find(conn, p.barcode)
        if row is None:
            raise ProductNotFoundError(product.barcode)
        conn.execute(
            "UPDATE products SET name = ?, price = ?, critical_stock_level = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE barcode = ?",
            (p.name, p.price, p.critical_stock_level, row["barcode"]),
        )


def set_cost(barcode: str, cost: float) -> float:
    """Set a product's unit cost by hand (Admin's product form). `cost` is a
    number >= 0 (0 = unknown), at most MAX_AMOUNT, rounded half-up to cents;
    raises ValueError (UserError) otherwise and ProductNotFoundError for an
    unknown barcode. Returns the stored cost. Sales already made keep the
    cost they were snapshotted with."""
    cost = clean_cost(cost)
    with connection_scope() as conn:
        row = _find(conn, barcode)
        if row is None:
            raise ProductNotFoundError(barcode)
        conn.execute(
            "UPDATE products SET cost_price = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE barcode = ?",
            (cost, row["barcode"]),
        )
    return cost


def costs_for(barcodes, conn: sqlite3.Connection | None = None) -> dict[str, tuple[float, bool]]:
    """What each product costs per unit right now, for the sale snapshot:
    {barcode as given: (cost_price, cost_known)}, cost_known being
    cost_price > 0. A barcode that matches no product maps to (0.0, False).
    Barcodes match in any letter case.

    Pass the open `conn` when calling from inside another repository's
    transaction (transaction_repository.finalize_transaction) so the cost
    read is part of the same snapshot; with no `conn` it opens its own."""
    wanted = list(dict.fromkeys(barcodes))

    def read(connection: sqlite3.Connection) -> dict[str, tuple[float, bool]]:
        found: dict[str, tuple[float, bool]] = {}
        for barcode in wanted:
            row = _find(connection, barcode)
            cost = float(row["cost_price"]) if row is not None else 0.0
            found[barcode] = (cost, cost > 0)
        return found

    if conn is not None:
        return read(conn)
    with connection_scope() as own:
        return read(own)


def set_active(barcode: str, active: bool) -> None:
    """Deactivate (False) or reactivate (True) a product. Stock and history
    are untouched; it just stops being sellable / receivable / shippable.
    Raises ProductNotFoundError."""
    with connection_scope() as conn:
        row = _find(conn, barcode)
        if row is None:
            raise ProductNotFoundError(barcode)
        conn.execute(
            "UPDATE products SET is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE barcode = ?",
            (int(bool(active)), row["barcode"]),
        )


def delete(barcode: str) -> None:
    """Remove a product that has never been used. Raises
    ProductNotFoundError if it doesn't exist and ProductInUseError if it
    still has stock anywhere (shelves, warehouses, a truck) or any history
    (stock movements, sales, shipment lines) - deleting would destroy stock
    without a movement or break the records that point at it. Deactivate
    such a product instead (set_active)."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _find(conn, barcode)
            if row is None:
                raise ProductNotFoundError(barcode)
            code = row["barcode"]
            held = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) FROM stock_levels WHERE product_barcode = ?", (code,)
            ).fetchone()[0]
            if held > 0 or row["stock_quantity"] > 0:
                units = max(int(held), int(row["stock_quantity"]))
                raise ProductInUseError(code, f"{units} units are still in stock", "err.reason.stock", n=units)
            for table, what, what_key in (
                ("stock_movements", "it has stock movements on record", "err.reason.movements"),
                ("transaction_items", "it appears on past sales", "err.reason.sales"),
                ("shipment_lines", "it appears on shipments", "err.reason.shipments"),
                ("purchase_orders", "it has purchase orders", "err.reason.purchase_orders"),
            ):
                if conn.execute(f"SELECT 1 FROM {table} WHERE product_barcode = ? LIMIT 1", (code,)).fetchone():
                    raise ProductInUseError(code, what, what_key)
            conn.execute("DELETE FROM stock_levels WHERE product_barcode = ?", (code,))
            conn.execute("DELETE FROM products WHERE barcode = ?", (code,))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


# --- the cart at checkout ------------------------------------------------------

@dataclass(frozen=True)
class CartIssue:
    """Something about a cart line that is no longer true. kind: 'price'
    (changed since it was added), 'unavailable' (deleted/deactivated) or
    'stock' (the shelf holds fewer units than the cart wants)."""

    kind: str
    barcode: str
    name: str
    cart_price: float
    current_price: float | None
    available: int
    message: str


def cart_issues(lines: list[tuple[str, float, int]], current: dict[str, Product | None]) -> list[CartIssue]:
    """Pure part of check_cart: `lines` is [(barcode, unit price in the
    cart, quantity)], `current` maps barcode -> the product as the database
    holds it NOW at this location (stock_quantity = units here), or None if
    it no longer exists. One issue per problem line, in cart order."""
    issues: list[CartIssue] = []
    wanted: dict[str, int] = {}
    for barcode, _price, quantity in lines:
        wanted[barcode] = wanted.get(barcode, 0) + int(quantity)
    seen: set[str] = set()
    for barcode, price, _quantity in lines:
        if barcode in seen:
            continue
        seen.add(barcode)
        now = current.get(barcode)
        if now is None or not now.is_active:
            name = now.name if now is not None else barcode
            issues.append(CartIssue("unavailable", barcode, name, price, None, 0,
                                    f"{name} is no longer sold - it was removed or deactivated."))
        elif round(float(now.price), 2) != round(float(price), 2):
            issues.append(CartIssue("price", barcode, now.name, price, float(now.price), now.stock_quantity,
                                    f"The price of {now.name} changed from {format_money(price, '$')} to {format_money(now.price, '$')}."))
        elif wanted[barcode] > now.stock_quantity:
            issues.append(CartIssue("stock", barcode, now.name, price, float(now.price), now.stock_quantity,
                                    f"Only {now.stock_quantity} of {now.name} left on this shelf."))
    return issues


def check_cart(location: StockLocation, lines: list[tuple[str, float, int]]) -> list[CartIssue]:
    """Re-read every cart line's CURRENT price, active flag and stock at
    `location` from the database and return what no longer holds (empty
    list: safe to finalize). Call right before finalizing a sale."""
    current: dict[str, Product | None] = {}
    with connection_scope() as conn:
        for barcode, _price, _quantity in lines:
            row = _find(conn, barcode)
            if row is None:
                current[barcode] = None
                continue
            product = _row_to_product(row)
            product.stock_quantity = level_in(conn, location, row["barcode"])
            current[barcode] = product
            if row["barcode"] != barcode:
                current[row["barcode"]] = product
    return cart_issues(lines, current)
