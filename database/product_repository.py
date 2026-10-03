"""All Product CRUD/queries. The only place SQL for `products` is allowed to live.

`pos_app`, `depot_app`, and `admin_app` call these functions - they
never write SQL of their own, and never import each other to reach this
indirectly. Every function returns/accepts `shared.models.Product`
instances, not raw sqlite3.Row objects, so callers never need to know
the storage is SQLite.
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import DuplicateBarcodeError, ProductNotFoundError
from shared.models import Product


def _row_to_product(row: sqlite3.Row) -> Product:
    return Product(
        barcode=row["barcode"],
        name=row["name"],
        price=row["price"],
        stock_quantity=row["stock_quantity"],
        critical_stock_level=row["critical_stock_level"],
    )


def get_by_barcode(barcode: str) -> Product:
    """Return the Product for `barcode`, or raise ProductNotFoundError."""
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE barcode = ?", (barcode,)
        ).fetchone()
    if row is None:
        raise ProductNotFoundError(barcode)
    return _row_to_product(row)


def list_all() -> list[Product]:
    """Return every product, e.g. for the Admin product management tab."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    return [_row_to_product(row) for row in rows]


def get_critical_stock_list() -> list[Product]:
    """Return products at or below their critical_stock_level.

    The one query behind both of depot_app's and admin_app's low-stock
    views - see shared/gui_kit/polling.py for how depot_app re-runs this
    on an interval to keep its alert panel current, and
    admin_app/gui/inventory_health_tab.py for the passive equivalent.
    """
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE stock_quantity <= critical_stock_level "
            "ORDER BY name"
        ).fetchall()
    return [_row_to_product(row) for row in rows]


def create(product: Product) -> None:
    """Insert a new product. Raises DuplicateBarcodeError if the barcode exists.

    Any starting `stock_quantity` is recorded as UNASSIGNED stock (the
    product form doesn't ask where it is); Admin > Warehouses moves it to
    a place. One transaction, so the total and the level never disagree.
    """
    if int(product.stock_quantity) < 0:
        raise ValueError("stock_quantity can't be negative")
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO products "
                "(barcode, name, price, stock_quantity, critical_stock_level) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    product.barcode,
                    product.name,
                    product.price,
                    product.stock_quantity,
                    product.critical_stock_level,
                ),
            )
            if product.stock_quantity:
                conn.execute(
                    "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
                    "VALUES ('unassigned', '', ?, ?)",
                    (product.barcode, int(product.stock_quantity)),
                )
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise DuplicateBarcodeError(product.barcode) from exc
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def update(product: Product) -> None:
    """Update an existing product's fields (name/price/critical_stock_level).

    Stock quantity is intentionally NOT updated here - it's the company
    total of the per-location levels, and only ever changes through
    database/stock_repository.py (receipts, dispatches, counts,
    transfers), a sale (transaction_repository) or a shipment
    (shipment_repository), each with an audit row - never a silent edit.
    """
    with connection_scope() as conn:
        cursor = conn.execute(
            "UPDATE products SET name = ?, price = ?, critical_stock_level = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE barcode = ?",
            (product.name, product.price, product.critical_stock_level, product.barcode),
        )
    if cursor.rowcount == 0:
        raise ProductNotFoundError(product.barcode)


def delete(barcode: str) -> None:
    """Remove a product. Raises ProductNotFoundError if it doesn't exist."""
    with connection_scope() as conn:
        cursor = conn.execute("DELETE FROM products WHERE barcode = ?", (barcode,))
    if cursor.rowcount == 0:
        raise ProductNotFoundError(barcode)
