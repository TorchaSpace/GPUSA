"""Purchase orders and the safe purchase price bands. Same rules as the desktop's
purchase_order_repository.py:

- An administrator sets a safe unit-price band per product. The depot raises an order (product,
  supplier, quantity, unit price, site). The server judges the price against the band: inside it
  the order is "sent" at once; outside it, or with no band, it is "pending" (held) until an
  administrator approves it ("sent") or rejects it. Two admins deciding at once: only the first wins.
- A delivery is booked against an order in ONE transaction: received quantity and status
  (partially_received / received), the units on the warehouse shelf and the company total, a 'receive'
  movement that names the order, and the product's weighted-average cost. Receiving more than is due,
  against an order that is not sent / partly received, or into a full or switched-off warehouse is refused.
- A delivery at the depot Floor is booked against the open orders for that product and site, oldest
  first; whatever is beyond what they still await is an ordinary receipt.
- An order that is not fully received can be cancelled: by an administrator while pending, sent or
  partly received (units already received stay), by a depot manager only their own pending ones.

Who: administrators set bands and decide held orders. Administrators and depot managers (admin area or
depot console) raise, receive and cancel, and read.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import activity, authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_AMOUNT, MAX_UNITS
from .shipment_routes import DEPOT_AREAS, DEPOT_ROLES
from .stock_routes import _change, _clean, _fail, _log, _lock_product, _require_can_hold, _require_place, _write

router = APIRouter(tags=["purchasing"])

ADMINS = frozenset({"admin", "depot_manager", "cashier"})  # Admin area: root + IAM users
ADMIN_AREA = frozenset({"admin"})
MAX_SUPPLIER = 200
_CENT = Decimal("0.01")
_OPEN = ("pending", "sent", "partially_received")
_RECEIVABLE = ("sent", "partially_received")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _site(text: str | None) -> str:
    """Sites are stored squashed and upper-case, so "wh-01" and "WH-01" are one."""
    return " ".join((text or "").split()).upper()


def site_code(text: str | None) -> str:
    """"WH-01" out of a "WH-01 · Main" label; a label without a dot is its own code."""
    return (text or "").split("·")[0].strip().upper()


def weighted_average_cost(on_hand: int, old_cost: Decimal, received: int, unit_price: Decimal) -> Decimal:
    """(on_hand * old + received * price) / (on_hand + received); the price alone when nothing is on hand or no
    cost is on record (0 = unknown, not free). Rounded to cents, half-up."""
    on_hand = max(0, int(on_hand))
    if on_hand == 0 or old_cost <= 0:
        new = unit_price
    else:
        new = (on_hand * old_cost + received * unit_price) / (on_hand + received)
    return Decimal(new).quantize(_CENT, rounding=ROUND_HALF_UP)


def hold_reason_for(band: dict | None, unit_price: Decimal) -> str | None:
    if band is None:
        return "no_range"
    if unit_price > band["max_unit_price"]:
        return "above_range"
    if unit_price < band["min_unit_price"]:
        return "below_range"
    return None


def number(order_id: int) -> str:
    return f"PO-{order_id:05d}"


# --- price bands ----------------------------------------------------------------------

class BandIn(BaseModel):
    min_unit_price: Decimal = Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    max_unit_price: Decimal = Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    default_supplier: str | None = Field(default=None, max_length=500)


class BandOut(BaseModel):
    product_barcode: str
    min_unit_price: float
    max_unit_price: float
    default_supplier: str | None


def _band_out(row) -> BandOut:
    return BandOut(product_barcode=row["product_barcode"], min_unit_price=float(row["min_unit_price"]),
                   max_unit_price=float(row["max_unit_price"]), default_supplier=row["default_supplier"])


def _readers(session: Session) -> None:
    authz.require(session, roles=DEPOT_ROLES, areas=frozenset({"admin", "depot_console"}))


def _audit(conn, session: Session, action: str, target: str, detail: str) -> None:
    conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) VALUES (%s, %s, 'price_band', %s, %s)",
                 (authz.actor_label(session), action, target, detail))


@router.get("/price-ranges", response_model=list[BandOut])
def list_bands(request: Request, session: Session = Depends(current_session)) -> list[BandOut]:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return [_band_out(r) for r in conn.execute("SELECT * FROM purchase_price_ranges ORDER BY product_barcode")]


@router.get("/price-ranges/{barcode}", response_model=BandOut | None)
def get_band(barcode: str, request: Request, session: Session = Depends(current_session)) -> BandOut | None:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT * FROM purchase_price_ranges WHERE upper(product_barcode) = %s",
                           (barcode.strip().upper(),)).fetchone()
        return _band_out(row) if row else None


@router.put("/price-ranges/{barcode}", response_model=BandOut)
def set_band(barcode: str, body: BandIn, request: Request, session: Session = Depends(current_session)) -> BandOut:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    low = _money(body.min_unit_price) if body.min_unit_price >= Decimal("0.005") else Decimal("0.00")
    high = _money(body.max_unit_price)
    if low > high:
        raise _fail(422, "The minimum price can't be above the maximum.")
    supplier = (body.default_supplier or "").strip() or None

    def work(conn):
        product = _lock_product(conn, barcode)
        code = product["barcode"]
        old = conn.execute("SELECT * FROM purchase_price_ranges WHERE product_barcode = %s FOR UPDATE", (code,)).fetchone()
        row = conn.execute(
            "INSERT INTO purchase_price_ranges (product_barcode, min_unit_price, max_unit_price, default_supplier) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (product_barcode) DO UPDATE SET min_unit_price = excluded.min_unit_price, "
            "max_unit_price = excluded.max_unit_price, default_supplier = excluded.default_supplier, updated_at = now() "
            "RETURNING *", (code, low, high, supplier)).fetchone()
        if old is None or (old["min_unit_price"], old["max_unit_price"], old["default_supplier"]) != (low, high, supplier):
            before = "" if old is None else f"{float(old['min_unit_price']):g}-{float(old['max_unit_price']):g} -> "
            _audit(conn, session, "created" if old is None else "changed", code, f"{before}{float(low):g}-{float(high):g}")
        return _band_out(row)

    return _write(request, session, ADMINS, work)


@router.delete("/price-ranges/{barcode}", status_code=204)
def delete_band(barcode: str, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        old = conn.execute("DELETE FROM purchase_price_ranges WHERE upper(product_barcode) = %s RETURNING *",
                           (barcode.strip().upper(),)).fetchone()
        if old is not None:
            _audit(conn, session, "deleted", old["product_barcode"],
                   f"{float(old['min_unit_price']):g}-{float(old['max_unit_price']):g}")

    _write(request, session, ADMINS, work)


# --- orders ---------------------------------------------------------------------------

class OrderIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    supplier: str = Field(max_length=1000)
    quantity: int = Field(gt=0, le=MAX_UNITS)
    unit_price: Decimal = Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)
    site: str = Field(max_length=500)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class ReceiveIn(BaseModel):
    quantity: int = Field(gt=0, le=MAX_UNITS)
    warehouse_code: str = Field(min_length=1, max_length=32)


class DeliveryIn(BaseModel):
    warehouse_code: str = Field(min_length=1, max_length=32)
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=MAX_UNITS)
    note: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=200)
    bin_code: str | None = Field(default=None, max_length=64)


class OrderOut(BaseModel):
    id: int
    product_barcode: str
    product_name: str
    supplier: str
    quantity: int
    unit_price: float
    site: str
    range_min: float | None
    range_max: float | None
    status: str
    hold_reason: str | None
    created_at: datetime
    decided_at: datetime | None
    decision_note: str | None
    raised_by: str | None
    decided_by: str | None
    received_qty: int
    received_at: datetime | None
    received_by: str | None
    cancelled_at: datetime | None
    cancelled_by: str | None


def _order_out(row) -> OrderOut:
    f = lambda v: None if v is None else float(v)  # noqa: E731
    return OrderOut(
        id=row["id"], product_barcode=row["product_barcode"], product_name=row["product_name_at_order"],
        supplier=row["supplier"], quantity=row["quantity"], unit_price=float(row["unit_price"]), site=row["site"],
        range_min=f(row["range_min"]), range_max=f(row["range_max"]), status=row["status"],
        hold_reason=row["hold_reason"], created_at=row["created_at"], decided_at=row["decided_at"],
        decision_note=row["decision_note"], raised_by=row["raised_by"], decided_by=row["decided_by"],
        received_qty=row["received_qty"], received_at=row["received_at"], received_by=row["received_by"],
        cancelled_at=row["cancelled_at"], cancelled_by=row["cancelled_by"])


def _locked_order(conn, order_id: int) -> dict:
    row = conn.execute("SELECT * FROM purchase_orders WHERE id = %s FOR UPDATE", (order_id,)).fetchone()
    if row is None:
        raise _fail(404, f"No purchase order with id {order_id}.")
    return row


def _live_role(conn, session: Session) -> str | None:
    row = conn.execute("SELECT role FROM accounts WHERE id = %s", (session.account_id,)).fetchone()
    return row["role"] if row else None


@router.get("/purchase-orders/pending-ids", response_model=list[int])
def pending_ids(request: Request, session: Session = Depends(current_session)) -> list[int]:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        own = authz.own_warehouse(conn, session)
        return [r["id"] for r in conn.execute("SELECT id, site FROM purchase_orders WHERE status = 'pending' ORDER BY id")
                if own is None or site_code(r["site"]) == own.upper()]


@router.get("/purchase-orders", response_model=list[OrderOut])
def list_orders(request: Request, status: str | None = None, site: str | None = None,
                limit: int | None = Query(default=None, ge=1, le=5000),
                session: Session = Depends(current_session)) -> list[OrderOut]:
    """Newest first, optionally by status and/or site (compared by warehouse code, so a renamed
    warehouse keeps its orders)."""
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute("SELECT * FROM purchase_orders WHERE (%s::text IS NULL OR status = %s) "
                            "ORDER BY created_at DESC, id DESC", (status, status)).fetchall()
        own = authz.own_warehouse(conn, session)
    if own is not None:   # a depot manager sees their own warehouse's orders only
        rows = [r for r in rows if site_code(r["site"]) == own.upper()]
    if site is not None:
        wanted = site_code(site)
        rows = [r for r in rows if site_code(r["site"]) == wanted]
    return [_order_out(r) for r in (rows[:limit] if limit else rows)]


@router.get("/purchase-orders/{order_id}", response_model=OrderOut)
def get_order(order_id: int, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT * FROM purchase_orders WHERE id = %s", (order_id,)).fetchone()
        if row is None:
            raise _fail(404, f"No purchase order with id {order_id}.")
        if not authz.same_warehouse(conn, session, row["site"]):
            raise _fail(404, f"No purchase order with id {order_id}.")
        return _order_out(row)


@router.post("/purchase-orders", response_model=OrderOut, status_code=201)
def submit(body: OrderIn, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    supplier = (body.supplier or "").strip()
    site = _site(body.site)
    if not supplier:
        raise _fail(422, "A supplier is required.")
    if len(supplier) > MAX_SUPPLIER:
        raise _fail(422, f"A supplier name can have at most {MAX_SUPPLIER} characters.")
    if not site:
        raise _fail(422, "A site is required.")
    price = _money(body.unit_price)
    if price < _CENT:
        raise _fail(422, "The unit price must be at least 0.01.")

    def work(conn):
        authz.scope_warehouse(conn, session, site)   # a depot manager orders for their own warehouse only
        product = _lock_product(conn, body.barcode)
        if not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off; it cannot be ordered.")
        band = conn.execute("SELECT * FROM purchase_price_ranges WHERE product_barcode = %s",
                            (product["barcode"],)).fetchone()
        reason = hold_reason_for(band, price)
        row = conn.execute(
            "INSERT INTO purchase_orders (product_barcode, product_name_at_order, supplier, quantity, unit_price, site, "
            "range_min, range_max, status, hold_reason, raised_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (product["barcode"], product["name"], supplier, body.quantity, price, site,
             band["min_unit_price"] if band else None, band["max_unit_price"] if band else None,
             "pending" if reason else "sent", reason, authz.actor_label(session))).fetchone()
        activity.record(conn, "po_pending" if reason else "po_sent", severity="warning" if reason else "info",
                        source="depot", actor=authz.actor_label(session), number=number(row["id"]),
                        product=product["name"], quantity=body.quantity, total=float(body.quantity * price), site=site)
        return _order_out(row)

    return _write(request, session, DEPOT_ROLES, work)


def _decide(order_id: int, new_status: Literal["sent", "rejected"], body: NoteIn, request: Request,
            session: Session) -> OrderOut:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _locked_order(conn, order_id)
        if row["status"] != "pending":
            raise _fail(409, f"{number(order_id)} was decided already: it is {row['status']}.")
        updated = conn.execute(
            "UPDATE purchase_orders SET status = %s, decision_note = %s, decided_by = %s, decided_at = now() "
            "WHERE id = %s RETURNING *", (new_status, _clean(body.note), authz.actor_label(session), order_id)).fetchone()
        activity.record(conn, "po_approved" if new_status == "sent" else "po_rejected", source="admin",
                        actor=authz.actor_label(session), number=number(order_id), product=row["product_name_at_order"],
                        quantity=row["quantity"])
        return _order_out(updated)

    return _write(request, session, ADMINS, work)


@router.post("/purchase-orders/{order_id}/approve", response_model=OrderOut)
def approve(order_id: int, body: NoteIn, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    return _decide(order_id, "sent", body, request, session)


@router.post("/purchase-orders/{order_id}/reject", response_model=OrderOut)
def reject(order_id: int, body: NoteIn, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    return _decide(order_id, "rejected", body, request, session)


def _receive_units(conn, kind: str, code: str, barcode: str, quantity: int, *, note: str | None, actor: str,
                   reference: str | None, bin_code: str | None) -> None:
    """Inbound stock at a warehouse: switched on, has room, level and company total up, one 'receive' movement."""
    _require_place(conn, kind, code)
    _require_can_hold(conn, kind, code, quantity)
    _change(conn, kind, code, barcode, quantity, change_total=True)
    _log(conn, kind, code, barcode, "receive", quantity, reason="receive", actor=actor, note=note,
         reference=reference, bin_code=bin_code)


def _book_against(conn, row: dict, quantity: int, code: str, actor: str, *, note: str | None = None,
                  bin_code: str | None = None) -> None:
    """The write half of a delivery against one order, inside the caller's transaction."""
    product = _lock_product(conn, row["product_barcode"])
    stock = conn.execute("SELECT stock_quantity, cost_price FROM products WHERE barcode = %s",
                         (product["barcode"],)).fetchone()
    if not product["is_active"]:
        raise _fail(409, f"Product {product['barcode']} is switched off; it cannot receive stock.")
    conn.execute(
        "UPDATE purchase_orders SET received_qty = received_qty + %s, "
        "status = CASE WHEN received_qty + %s >= quantity THEN 'received' ELSE 'partially_received' END, "
        "received_at = now(), received_by = %s WHERE id = %s", (quantity, quantity, actor, row["id"]))
    _receive_units(conn, "warehouse", code, product["barcode"], quantity, actor=actor, reference=number(row["id"]),
                   note=note or f"Received against {number(row['id'])} ({row['supplier']})", bin_code=bin_code)
    conn.execute("UPDATE products SET cost_price = %s, updated_at = now() WHERE barcode = %s",
                 (weighted_average_cost(stock["stock_quantity"], stock["cost_price"], quantity, row["unit_price"]),
                  product["barcode"]))


@router.post("/purchase-orders/{order_id}/receive", response_model=OrderOut)
def receive(order_id: int, body: ReceiveIn, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    """Book a delivery of `quantity` units against an order, into a warehouse. All or nothing."""
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    code = body.warehouse_code.strip().upper()

    def work(conn):
        authz.scope_warehouse(conn, session, code)
        row = _locked_order(conn, order_id)
        if not authz.same_warehouse(conn, session, row["site"]):
            raise _fail(404, f"No purchase order with id {order_id}.")
        if row["status"] not in _RECEIVABLE:
            raise _fail(409, f"{number(order_id)} is {row['status']}; it cannot be received now.")
        remaining = row["quantity"] - row["received_qty"]
        if body.quantity > remaining:
            raise _fail(409, f"{number(order_id)} awaits {remaining} more units; {body.quantity} were booked.")
        _book_against(conn, row, body.quantity, code, authz.actor_label(session))
        return _order_out(conn.execute("SELECT * FROM purchase_orders WHERE id = %s", (order_id,)).fetchone())

    return _write(request, session, DEPOT_ROLES, work)


@router.post("/purchase-orders/receive-delivery")
def receive_delivery(body: DeliveryIn, request: Request, session: Session = Depends(current_session)) -> dict:
    """Goods arrive at a warehouse's Floor. Booked against this depot's open orders for the product, oldest
    first; whatever is beyond what they await is an ordinary receipt. Returns the order numbers it settled."""
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    code = body.warehouse_code.strip().upper()
    actor = authz.actor_label(session)

    def work(conn):
        authz.scope_warehouse(conn, session, code)
        product = _lock_product(conn, body.barcode)
        if not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off; it cannot receive stock.")
        _require_place(conn, "warehouse", code)
        left, numbers = body.quantity, []
        for row in conn.execute("SELECT * FROM purchase_orders WHERE product_barcode = %s AND status = ANY(%s) ORDER BY id "
                                "FOR UPDATE", (product["barcode"], list(_RECEIVABLE))).fetchall():
            if left <= 0:
                break
            if site_code(row["site"]) != code:
                continue
            take = min(left, row["quantity"] - row["received_qty"])
            if take <= 0:
                continue
            _book_against(conn, row, take, code, actor,
                          note=_clean(body.note) or f"Received at the Floor against {number(row['id'])} ({row['supplier']})",
                          bin_code=_clean(body.bin_code))
            numbers.append(number(row["id"]))
            left -= take
        if left > 0:
            _receive_units(conn, "warehouse", code, product["barcode"], left, note=_clean(body.note), actor=actor,
                           reference=_clean(body.reference), bin_code=_clean(body.bin_code))
        return {"orders": numbers}

    return _write(request, session, DEPOT_ROLES, work)


@router.post("/purchase-orders/{order_id}/cancel", response_model=OrderOut)
def cancel(order_id: int, request: Request, session: Session = Depends(current_session)) -> OrderOut:
    """An administrator cancels a pending, sent or partly received order; a depot manager only their own
    pending ones. The role is read from the account now, not from the token."""
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)

    def work(conn):
        row = _locked_order(conn, order_id)
        if not authz.same_warehouse(conn, session, row["site"]):
            raise _fail(404, f"No purchase order with id {order_id}.")
        role = _live_role(conn, session)
        if row["status"] not in _OPEN:
            raise _fail(409, f"{number(order_id)} is {row['status']}; it cannot be cancelled now.")
        if role == "depot_manager":
            if row["status"] != "pending":
                raise _fail(403, f"Only an administrator can cancel {number(order_id)} once it is {row['status']}.")
            mine = (row["raised_by"] or "").rpartition("·")[2].strip().upper() == session.badge_id.upper()
            if not mine:
                raise _fail(403, f"{number(order_id)} was raised by someone else; only they or an administrator can cancel it.")
        elif role != "admin":
            raise _fail(403, "Not allowed.")
        updated = conn.execute("UPDATE purchase_orders SET status = 'cancelled', cancelled_at = now(), cancelled_by = %s "
                               "WHERE id = %s RETURNING *", (authz.actor_label(session), order_id)).fetchone()
        return _order_out(updated)

    return _write(request, session, DEPOT_ROLES, work)
