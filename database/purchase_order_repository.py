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
5. When the goods arrive, the depot books them against the order
   (receive_against_order()): in ONE transaction the order's received
   quantity moves ("sent" / "partially_received" -> "partially_received" /
   "received"), the units go into the warehouse's stock with a movement row
   (stock_repository.receive_in), and the product's weighted-average cost
   is updated (shared.costing). Over-receiving, receiving against an order
   in any other state, or into a full / inactive warehouse is refused and
   nothing is written.
6. cancel_order() withdraws an order that isn't fully received: an
   administrator may cancel a pending, sent or partially received one (units
   already received stay in stock; nothing more can be received); a depot
   manager only their own pending ones.

Both writes take BEGIN IMMEDIATE and use conditional UPDATEs checked by
rowcount, so two machines acting on one order can't both win.

Deliberately not in this v1 (see architecture.md): per-role approval limits
/ manager tolerance / escalation timers from the Settings mockup, and a
suppliers table (supplier is free text, as in the mockup's own form).
"""

from __future__ import annotations

import functools
import sqlite3

from shared.formatting import format_int
from shared.i18n import UserError
from database.connection import connection_scope
from database import account_repository, activity_repository, stock_repository
from database.exceptions import (
    DataAccessError,
    ProductNotFoundError,
    PurchaseOrderAlreadyDecidedError,
    PurchaseOrderCancelNotAllowedError,
    PurchaseOrderNotFoundError,
    PurchaseOrderOverReceiveError,
    PurchaseOrderStateError,
)
from database.ledger_repository import normalise_site
from shared.auth import Actor, actor_label, normalize_badge_id
from shared.costing import weighted_average_cost
from shared.formatting import round_money
from shared.models import RECEIVABLE_STATUSES, PriceRange, PurchaseOrder, StockLocation, hold_reason_for

# Largest quantity on one order - a typo guard (an extra zero), not a business rule.
MAX_QUANTITY = 1_000_000
MAX_SUPPLIER_LENGTH = 200


def _wrap_sqlite(func):
    """A stray sqlite3.Error becomes a DataAccessError - callers never
    see SQLite exceptions. ValueError and our own errors pass through."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as exc:
            raise DataAccessError(f"Database error: {exc}") from exc

    return wrapper


# --- Safe price ranges ---------------------------------------------------


def _row_to_range(row: sqlite3.Row) -> PriceRange:
    return PriceRange(
        product_barcode=row["product_barcode"],
        min_unit_price=row["min_unit_price"],
        max_unit_price=row["max_unit_price"],
        default_supplier=row["default_supplier"],
    )


def _validate_range(price_range: PriceRange) -> tuple[float, float]:
    """Finite, non-negative, <= MAX_AMOUNT, rounded half-up to cents;
    returns (min, max). Raises ValueError."""
    import math

    for value in (price_range.min_unit_price, price_range.max_unit_price):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise UserError("err.prices_finite")
        if value < 0:
            raise UserError("err.prices_negative")
    low = round_money(price_range.min_unit_price, "Minimum price") if price_range.min_unit_price >= 0.005 else 0.0
    high = round_money(price_range.max_unit_price, "Maximum price")
    if low > high:
        raise UserError("err.min_gt_max")
    return low, high


@_wrap_sqlite
def get_price_range(barcode: str) -> PriceRange | None:
    """The product's safe band, or None if no band has been set."""
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM purchase_price_ranges WHERE product_barcode = ?", (barcode,)
        ).fetchone()
    return _row_to_range(row) if row is not None else None


@_wrap_sqlite
def list_price_ranges() -> list[PriceRange]:
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT * FROM purchase_price_ranges ORDER BY product_barcode"
        ).fetchall()
    return [_row_to_range(row) for row in rows]


@_wrap_sqlite
def set_price_range(price_range: PriceRange) -> None:
    """Create or replace a product's band. Raises ProductNotFoundError for
    an unknown barcode, ValueError for negative/inverted prices. Existing
    orders keep the band they were judged against (see schema.sql)."""
    low, high = _validate_range(price_range)
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
            (price_range.product_barcode, low, high, supplier),
        )


@_wrap_sqlite
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
        received_qty=row["received_qty"],
        received_at=row["received_at"],
        received_by=row["received_by"],
        cancelled_at=row["cancelled_at"],
        cancelled_by=row["cancelled_by"],
    )


@_wrap_sqlite
def submit(barcode: str, supplier: str, quantity: int, unit_price: float, site: str,
           raised_by: Actor | None = None) -> PurchaseOrder:
    """Raise a purchase order and decide, authoritatively, whether it's
    sent directly or held for approval. Returns the stored order.

    Raises ValueError for a blank supplier/site, a quantity that isn't a
    whole number between 1 and MAX_QUANTITY, or a price that isn't finite,
    rounds (half-up, to cents) to less than 0.01 or exceeds MAX_AMOUNT;
    ProductNotFoundError for an unknown barcode. The site is stored
    stripped and upper-cased.

    BEGIN IMMEDIATE so the band that's read and the order that's written
    are one consistent snapshot - an admin changing the band at the same
    moment can't produce an order stamped with one band but judged
    against another.
    """
    supplier = (supplier or "").strip()
    site = normalise_site(site)
    if not supplier:
        raise UserError("err.supplier_required")
    if len(supplier) > MAX_SUPPLIER_LENGTH:
        raise UserError("err.supplier_too_long", n=MAX_SUPPLIER_LENGTH)
    if not site:
        raise UserError("err.po_site_required")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise UserError("err.qty_whole")
    if quantity <= 0:
        raise UserError("err.qty_positive")
    if quantity > MAX_QUANTITY:
        raise UserError("err.qty_too_big", n=format_int(MAX_QUANTITY))
    unit_price = round_money(unit_price, "Unit price")

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
            activity_repository.record(
                conn, "po_pending" if hold_reason else "po_sent", severity="warning" if hold_reason else "info",
                source="depot", actor=raised_by, number=_row_to_order(row).number, product=product["name"],
                quantity=quantity, total=round(quantity * unit_price, 2), site=site)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return _row_to_order(row)


@_wrap_sqlite
def get(order_id: int) -> PurchaseOrder:
    with connection_scope() as conn:
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise PurchaseOrderNotFoundError(order_id)
    return _row_to_order(row)


@_wrap_sqlite
def list_orders(status: str | None = None, site: str | None = None, limit: int | None = None) -> list[PurchaseOrder]:
    """Orders newest first, optionally filtered by status and/or site
    (compared case-insensitively, so rows saved before sites were
    normalised still match)."""
    clauses, params = [], []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM purchase_orders"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None and site is None:
        sql += " LIMIT ?"
        params.append(limit)
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
    orders = [_row_to_order(row) for row in rows]
    if site is not None:
        wanted = normalise_site(site)
        orders = [o for o in orders if normalise_site(o.site) == wanted]
        if limit is not None:
            orders = orders[:limit]
    return orders


def list_pending() -> list[PurchaseOrder]:
    """Every order awaiting an admin decision, newest first."""
    return list_orders(status="pending")


@_wrap_sqlite
def pending_ids() -> list[int]:
    """Ids of every order awaiting a decision, ascending - cheap to poll,
    and (unlike a count) changes when one is decided AND another raised
    between two polls."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT id FROM purchase_orders WHERE status = 'pending' ORDER BY id").fetchall()
    return [int(row[0]) for row in rows]


@_wrap_sqlite
def count_pending() -> int:
    """Cheap enough to poll (indexed) - drives admin_app's sidebar badge."""
    with connection_scope() as conn:
        row = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status = 'pending'").fetchone()
    return int(row[0])


@_wrap_sqlite
def _decide(order_id: int, new_status: str, note: str | None, decided_by: Actor | None = None) -> PurchaseOrder:
    decider = actor_label(decided_by) if decided_by is not None else None
    if not decider or not decider.strip(" ·"):
        raise UserError("err.decision_needs_admin")
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
            (new_status, note, decider, order_id),
        )
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise PurchaseOrderNotFoundError(order_id)
    order = _row_to_order(row)
    if cursor.rowcount == 0:
        raise PurchaseOrderAlreadyDecidedError(order.number, order.status)
    with connection_scope() as conn:
        activity_repository.record(conn, "po_approved" if new_status == "sent" else "po_rejected", source="admin",
                                   actor=decided_by, number=order.number, product=order.product_name,
                                   quantity=order.quantity)
    return order


def approve(order_id: int, note: str | None = None, decided_by: Actor | None = None) -> PurchaseOrder:
    """Approve a held order - it becomes "sent". `decided_by` is required
    (ValueError without it: decided_by is never NULL on a decided order).
    Raises PurchaseOrderAlreadyDecidedError if it isn't pending anymore."""
    return _decide(order_id, "sent", note, decided_by)


def reject(order_id: int, note: str | None = None, decided_by: Actor | None = None) -> PurchaseOrder:
    """Reject a held order - it's never sent. `decided_by` is required
    (ValueError without it). Raises PurchaseOrderAlreadyDecidedError if
    it isn't pending anymore."""
    return _decide(order_id, "rejected", note, decided_by)


# --- Receiving and cancelling ---------------------------------------------

_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _acting_label(actor: Actor | None, key: str) -> str:
    label = actor_label(actor) if actor is not None else None
    if not label or not label.strip(" ·"):
        raise UserError(key)
    return label


def _in_transaction(fn):
    """Run fn(conn) inside one BEGIN IMMEDIATE transaction (rolled back on
    any error) and return its result."""
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


def _order_row(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM purchase_orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise PurchaseOrderNotFoundError(order_id)
    return row


@_wrap_sqlite
def receive_against_order(order_id: int, quantity: int, actor: Actor | None,
                          warehouse_location: StockLocation) -> PurchaseOrder:
    """Book a delivery of `quantity` units against an order, into
    `warehouse_location`. Returns the updated order.

    All or nothing, in one transaction: the order's received quantity and
    status (partially_received, or received once it reaches the ordered
    quantity), the warehouse's stock level and the company total, the
    'receive' stock movement (referencing the PO number) and the product's
    new weighted-average cost. If anything fails - warehouse inactive or
    full, product deactivated - none of it is kept.

    Raises ValueError (UserError) for a missing actor, a quantity that
    isn't a whole number above 0, or a location that isn't a warehouse;
    PurchaseOrderNotFoundError; PurchaseOrderStateError unless the order is
    'sent' (approved) or 'partially_received';
    PurchaseOrderOverReceiveError if `quantity` is more than is still due;
    LocationInactiveError / CapacityExceededError / UnknownLocationError
    from the warehouse."""
    received_by = _acting_label(actor, "err.po_receive_needs_actor")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise UserError("err.qty_whole")
    if quantity <= 0:
        raise UserError("err.qty_positive")
    if not isinstance(warehouse_location, StockLocation) or warehouse_location.kind != "warehouse":
        raise UserError("err.po_receive_warehouse")

    def run(conn):
        row = _order_row(conn, order_id)
        order = _row_to_order(row)
        if row["status"] not in RECEIVABLE_STATUSES:
            raise PurchaseOrderStateError(order.number, row["status"], "received")
        remaining = int(row["quantity"]) - int(row["received_qty"])
        if quantity > remaining:
            raise PurchaseOrderOverReceiveError(order.number, quantity, remaining)

        product = conn.execute(
            "SELECT stock_quantity, cost_price FROM products WHERE barcode = ?", (row["product_barcode"],)
        ).fetchone()
        if product is None:
            raise ProductNotFoundError(row["product_barcode"])
        on_hand_before, old_cost = int(product["stock_quantity"]), float(product["cost_price"])

        # The conditional UPDATE is the real guard (status and quantity
        # re-checked by the same statement that changes them); the reads
        # above only give clear messages.
        cursor = conn.execute(
            "UPDATE purchase_orders SET received_qty = received_qty + ?, "
            "status = CASE WHEN received_qty + ? >= quantity THEN 'received' ELSE 'partially_received' END, "
            f"received_at = {_NOW}, received_by = ? "
            "WHERE id = ? AND status IN ('sent', 'partially_received') AND received_qty + ? <= quantity",
            (quantity, quantity, received_by, order_id, quantity),
        )
        if cursor.rowcount == 0:
            raise PurchaseOrderStateError(order.number, row["status"], "received")

        stock_repository.receive_in(
            conn, warehouse_location, row["product_barcode"], quantity,
            note=f"Received against {order.number} ({row['supplier']})", actor=actor, reference=order.number,
        )
        conn.execute(
            f"UPDATE products SET cost_price = ?, updated_at = {_NOW} WHERE barcode = ?",
            (weighted_average_cost(on_hand_before, old_cost, quantity, float(row["unit_price"])),
             row["product_barcode"]),
        )
        return _row_to_order(_order_row(conn, order_id))

    return _in_transaction(run)


@_wrap_sqlite
def cancel_order(order_id: int, actor: Actor | None) -> PurchaseOrder:
    """Cancel an order that isn't fully received. Returns the updated order.

    Who may: an administrator - a pending, sent (approved) or partially
    received order (the units already received stay in stock; nothing more
    can be received); a depot manager - only their OWN pending orders. The
    role is read from the actor's account in the database, not trusted from
    the caller.

    Raises ValueError (UserError) without an actor; PurchaseOrderNotFoundError;
    PurchaseOrderStateError if the order is received, rejected or already
    cancelled (also when another machine got there first);
    PurchaseOrderCancelNotAllowedError when this person may not cancel it."""
    cancelled_by = _acting_label(actor, "err.po_cancel_needs_actor")

    def run(conn):
        row = _order_row(conn, order_id)
        order = _row_to_order(row)
        status = row["status"]
        role = account_repository.live_role(conn, actor.badge_id)
        if role not in ("admin", "depot_manager"):
            raise PurchaseOrderCancelNotAllowedError(order.number, status, "role")
        if status not in ("pending", "sent", "partially_received"):
            raise PurchaseOrderStateError(order.number, status, "cancelled")
        if role == "depot_manager":
            if status != "pending":
                raise PurchaseOrderCancelNotAllowedError(order.number, status, "admin_only")
            if order.raised_by_badge != normalize_badge_id(actor.badge_id):
                raise PurchaseOrderCancelNotAllowedError(order.number, status, "not_yours")
        cursor = conn.execute(
            f"UPDATE purchase_orders SET status = 'cancelled', cancelled_at = {_NOW}, cancelled_by = ? "
            "WHERE id = ? AND status = ?",
            (cancelled_by, order_id, status),
        )
        if cursor.rowcount == 0:
            raise PurchaseOrderStateError(order.number, _order_row(conn, order_id)["status"], "cancelled")
        return _row_to_order(_order_row(conn, order_id))

    return _in_transaction(run)
