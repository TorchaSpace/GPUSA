"""All SQL for the Purchase Requests / Purchase Orders domain - the
`purchase_price_ranges` and `purchase_orders` tables.

The workflow, taken from the Warehouse Console mockup's "Purchasing
Operations" tab (depot side) and the admin mockup's "Out-of-range purchase
requests" panel + Settings > "Safe Purchase Price Range" (admin side):

1. An administrator sets a safe unit-price band per product
   (set_price_range(), admin_app's Purchase Requests page).
2. The depot's purchasing manager raises an order - product, supplier,
   quantity, unit price (submit(), depot_app Manager Portal).
3. submit() judges the price against the product's band with
   shared.models.hold_reason_for(): inside the band -> status "sent"
   (placed with the supplier immediately); outside it, or no band set
   -> status "pending" (held; "not sent to the supplier until an
   administrator approves the price", as the mockup puts it).
4. An administrator approves (-> "sent") or rejects (-> "rejected") each
   held order (approve()/reject()).

Deliberately not in this v1 (see architecture.md): receiving goods
against an order (receiving still goes through inventory_repository's
receive_stock()), per-role approval limits / manager tolerance /
escalation timers from the Settings mockup (there's no auth or roles
system to hang them on), and a suppliers table (supplier is free text,
as in the mockup's own form).
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import (
    ProductNotFoundError,
    PurchaseOrderAlreadyDecidedError,
    PurchaseOrderNotFoundError,
)
from shared.auth import Actor, actor_label
from shared.models import PriceRange, PurchaseOrder, hold_reason_for


# --- Safe price ranges ---------------------------------------------------


def _row_to_range(row: sqlite3.Row) -> PriceRange:
    return PriceRange(
        product_barcode=row["product_barcode"],
        min_unit_price=row["min_unit_price"],
        max_unit_price=row["max_unit_price"],
        default_supplier=row["default_supplier"],
    )


def _validate_range(price_range: PriceRange) -> None:
    if price_range.min_unit_price < 0 or price_range.max_unit_price < 0:
        raise ValueError("Prices can't be negative.")
    if price_range.min_unit_price > price_range.max_unit_price:
        raise ValueError("The minimum price can't be higher than the maximum.")


def get_price_range(barcode: str) -> PriceRange | None:
    """The product's safe band, or None if no band has been set."""
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM purchase_price_ranges WHERE product_barcode = ?", (barcode,)
        ).fetchone()
    return _row_to_range(row) if row is not None else None


def list_price_ranges() -> list[PriceRange]:
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT * FROM purchase_price_ranges ORDER BY product_barcode"
        ).fetchall()
    return [_row_to_range(row) for row in rows]


def set_price_range(price_range: PriceRange) -> None:
    """Create or replace a product's band. Raises ProductNotFoundError for
    an unknown barcode, ValueError for negative/inverted prices. Existing
    orders keep the band they were judged against (see schema.sql)."""
    _validate_range(price_range)
    supplier = (price_range.default_supplier or "").strip() or None
    with connection_scope() as conn:
        exists = conn.execute(
            "SELECT 1 FROM products WHERE barcode = ?", (price_range.product_barcode,)
        ).fetchone()
        if exists is None:
            raise ProductNotFoundError(price_range.product_barcode)
        conn.execute(
            "INSERT INTO purchase_price_ranges "
            "(product_barcode, min_unit_price, max_unit_price, default_supplier) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(product_barcode) DO UPDATE SET "
            "min_unit_price = excluded.min_unit_price, max_unit_price = excluded.max_unit_price, "
            "default_supplier = excluded.default_supplier, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (price_range.product_barcode, price_range.min_unit_price, price_range.max_unit_price, supplier),
        )


def delete_price_range(barcode: str) -> None:
    """Remove a product's band (a no-op if it had none). Orders for that
    product are then held as "no_range" until a new band is set."""
    with connection_scope() as conn:
        conn.execute("DELETE FROM purchase_price_ranges WHERE product_barcode = ?", (barcode,))


# --- Purchase orders -----------------------------------------------------


def _row_to_order(row: sqlite3.Row) -> PurchaseOrder:
    return PurchaseOrder(
        id=row["id"],
        product_barcode=row["product_barcode"],
        product_name=row["product_name_at_order"],
        supplier=row["supplier"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        site=row["site"],
        range_min=row["range_min"],
        range_max=row["range_max"],
        status=row["status"],
        hold_reason=row["hold_reason"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        decision_note=row["decision_note"],
        raised_by=row["raised_by"],
        decided_by=row["decided_by"],
    )


def submit(barcode: str, supplier: str, quantity: int, unit_price: float, site: str,
           raised_by: Actor | None = None) -> PurchaseOrder:
    """Raise a purchase order and decide, authoritatively, whether it's
    sent directly or held for approval. Returns the stored order.

    Raises ValueError for a blank supplier/site or a non-positive
    quantity/price, ProductNotFoundError for an unknown barcode.

    BEGIN IMMEDIATE so the band that's read and the order that's written
    are one consistent snapshot - an admin changing the band at the same
    moment can't produce an order stamped with one band but judged
    against another.
    """
    supplier = (supplier or "").strip()
    site = (site or "").strip()
    if not supplier:
        raise ValueError("Enter a supplier.")
    if not site:
        raise ValueError("A purchase order needs a site.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than 0.")
    if unit_price <= 0:
        raise ValueError("Unit price must be greater than 0.")

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            product = conn.execute(
                "SELECT name FROM products WHERE barcode = ?", (barcode,)
            ).fetchone()
            if product is None:
                raise ProductNotFoundError(barcode)

            range_row = conn.execute(
                "SELECT * FROM purchase_price_ranges WHERE product_barcode = ?", (barcode,)
            ).fetchone()
            price_range = _row_to_range(range_row) if range_row is not None else None
            hold_reason = hold_reason_for(price_range, unit_price)
            status = "pending" if hold_reason else "sent"

            cursor = conn.execute(
                "INSERT INTO purchase_orders (product_barcode, product_name_at_order, supplier, "
                "quantity, unit_price, site, range_min, range_max, status, hold_reason, raised_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    barcode,
                    product["name"],
                    supplier,
                    quantity,
                    unit_price,
                    site,
                    price_range.min_unit_price if price_range else None,
                    price_range.max_unit_price if price_range else None,
                    status,
                    hold_reason,
                    actor_label(raised_by),
                ),
            )
            row = conn.execute(
                "SELECT * FROM purchase_orders WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return _row_to_order(row)


def get(order_id: int) -> PurchaseOrder:
    with connection_scope() as conn:
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise PurchaseOrderNotFoundError(order_id)
    return _row_to_order(row)


def list_orders(status: str | None = None, site: str | None = None, limit: int | None = None) -> list[PurchaseOrder]:
    """Orders newest first, optionally filtered by status and/or site."""
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if site is not None:
        clauses.append("site = ?")
        params.append(site)
    sql = "SELECT * FROM purchase_orders"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_order(row) for row in rows]


def list_pending() -> list[PurchaseOrder]:
    """Every order awaiting an admin decision, newest first."""
    return list_orders(status="pending")


def count_pending() -> int:
    """Cheap enough to poll (indexed) - drives admin_app's sidebar badge."""
    with connection_scope() as conn:
        row = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status = 'pending'").fetchone()
    return int(row[0])


def _decide(order_id: int, new_status: str, note: str | None, decided_by: Actor | None = None) -> PurchaseOrder:
    note = (note or "").strip() or None
    with connection_scope() as conn:
        # The `AND status = 'pending'` is what makes this safe with two
        # admins on two machines: only the first decision matches a row;
        # the second sees rowcount 0 and is told the order was already
        # decided, instead of silently flipping an approved order to
        # rejected (or vice versa).
        cursor = conn.execute(
            "UPDATE purchase_orders SET status = ?, decision_note = ?, decided_by = ?, "
            "decided_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE id = ? AND status = 'pending'",
            (new_status, note, actor_label(decided_by), order_id),
        )
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise PurchaseOrderNotFoundError(order_id)
    order = _row_to_order(row)
    if cursor.rowcount == 0:
        raise PurchaseOrderAlreadyDecidedError(order.number, order.status)
    return order


def approve(order_id: int, note: str | None = None, decided_by: Actor | None = None) -> PurchaseOrder:
    """Approve a held order - it becomes "sent". Raises
    PurchaseOrderAlreadyDecidedError if it isn't pending anymore."""
    return _decide(order_id, "sent", note, decided_by)


def reject(order_id: int, note: str | None = None, decided_by: Actor | None = None) -> PurchaseOrder:
    """Reject a held order - it's never sent. Raises
    PurchaseOrderAlreadyDecidedError if it isn't pending anymore."""
    return _decide(order_id, "rejected", note, decided_by)
