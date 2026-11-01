"""Sales from the tills: POST /sales and the sales list.

Same rules as the desktop's transaction_repository.finalize_transaction():
one transaction stores the sale and its lines (name, price and cost captured
at the moment of sale) and takes the units off the till's dealership shelf
and the company total, all or nothing.

`client_uuid` is the sale's key, made once by the till. A sale whose key is
already stored is NOT sold again: the stored sale comes back with
``duplicate: true``. That is what makes an offline till's upload safe to retry.

A live sale (no `sold_at`) must match today's prices to the cent (409 with
the changed lines otherwise), like the desktop. A sale made while the till was
offline (`sold_at` set) keeps the price the customer paid; it is still refused
(409) if the shelf does not hold the units, and the till keeps it aside for a
person to look at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_AMOUNT, MAX_UNITS

router = APIRouter(tags=["sales"])

SELLERS = frozenset({"admin", "cashier"})
POS_AREA = frozenset({"pos"})
READERS = frozenset({"admin", "cashier", "depot_manager"})
_CENT = Decimal("0.01")


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


class SaleLine(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    unit_price: Decimal = Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    quantity: int = Field(gt=0, le=MAX_UNITS)


class SaleIn(BaseModel):
    client_uuid: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    dealership_code: str | None = Field(default=None, max_length=32)
    payment_method: Literal["card", "cash"] | None = None
    lines: list[SaleLine] = Field(min_length=1, max_length=500)
    sold_at: datetime | None = None  # set only for a sale made offline and sent later


class SaleLineOut(BaseModel):
    barcode: str
    name: str
    unit_price: float
    quantity: int
    unit_cost: float
    cost_known: bool


class SaleOut(BaseModel):
    id: int
    client_uuid: str
    created_at: datetime
    total: float
    dealership_code: str | None
    cashier: str | None
    payment_method: str | None
    lines: list[SaleLineOut]
    duplicate: bool = False


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _read_sale(conn, sale_id: int, duplicate: bool = False) -> SaleOut:
    head = conn.execute("SELECT * FROM transactions WHERE id = %s", (sale_id,)).fetchone()
    lines = conn.execute("SELECT * FROM transaction_items WHERE transaction_id = %s ORDER BY id", (sale_id,)).fetchall()
    return SaleOut(
        id=head["id"], client_uuid=head["client_uuid"] or "", created_at=head["created_at"], total=float(head["total"]),
        dealership_code=head["dealership_code"], cashier=head["cashier"], payment_method=head["payment_method"],
        duplicate=duplicate,
        lines=[SaleLineOut(barcode=r["product_barcode"], name=r["product_name_at_sale"],
                           unit_price=float(r["unit_price_at_sale"]), quantity=r["quantity"],
                           unit_cost=float(r["unit_cost_at_sale"]), cost_known=r["cost_known"]) for r in lines])


@router.post("/sales", response_model=SaleOut)
def record_sale(body: SaleIn, request: Request, session: Session = Depends(current_session)) -> SaleOut:
    authz.require(session, roles=SELLERS, areas=POS_AREA)
    shop = (body.dealership_code or "").strip().upper() or None
    kind, code = ("dealership", shop) if shop else ("unassigned", "")
    offline = body.sold_at is not None
    wanted: dict[str, int] = {}
    for line in body.lines:
        key = line.barcode.strip().upper()
        wanted[key] = wanted.get(key, 0) + line.quantity

    try:
        with db.connect(request.app.state.settings) as conn:
            conn.row_factory = dict_row
            # The key first, before any other check: a re-sent sale is answered as
            # stored even if prices or stock have moved since.
            already = conn.execute("SELECT id FROM transactions WHERE client_uuid = %s", (body.client_uuid,)).fetchone()
            if already is not None:
                return _read_sale(conn, already["id"], duplicate=True)
            authz.require_live(conn, session, roles=SELLERS)
            if shop:
                place = conn.execute("SELECT name, is_active FROM dealerships WHERE code = %s FOR UPDATE",
                                     (shop,)).fetchone()
                if place is None:
                    raise _fail(404, f"No dealership with code {shop}.")
                if not place["is_active"] and not offline:
                    raise _fail(409, f"Dealership {place['name']} is switched off.")
            products: dict[str, dict] = {}
            for barcode in sorted(wanted):  # a fixed lock order, so two tills can't deadlock
                row = conn.execute("SELECT * FROM products WHERE upper(barcode) = %s FOR UPDATE", (barcode,)).fetchone()
                if row is None:
                    raise _fail(404, f"No product with barcode {barcode}.")
                if not row["is_active"] and not offline:
                    raise _fail(409, f"Product {row['barcode']} is switched off.")
                products[barcode] = row
            if not offline:
                moved = [f"{line.barcode}: {_money(line.unit_price)} -> {products[line.barcode.strip().upper()]['price']}"
                         for line in body.lines
                         if _money(line.unit_price) != products[line.barcode.strip().upper()]["price"]]
                if moved:
                    raise _fail(409, "Prices changed: " + "; ".join(moved))
            for barcode, quantity in wanted.items():
                stored = products[barcode]["barcode"]
                have = conn.execute("SELECT quantity FROM stock_levels WHERE location_kind = %s AND location_code = %s "
                                    "AND product_barcode = %s", (kind, code, stored)).fetchone()
                have = have["quantity"] if have else 0
                if quantity > have:
                    raise _fail(409, f"Only {have} units of {stored} at {code or 'Unassigned'}; the sale needs {quantity}.")

            total = sum((_money(line.unit_price) * line.quantity for line in body.lines), Decimal(0))
            created = body.sold_at.astimezone(timezone.utc) if offline else datetime.now(timezone.utc)
            head = conn.execute(
                "INSERT INTO transactions (created_at, total, dealership_code, cashier, payment_method, client_uuid) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (created, total, shop, authz.actor_label(session), body.payment_method, body.client_uuid)).fetchone()
            for line in body.lines:
                product = products[line.barcode.strip().upper()]
                cost = product["cost_price"]
                conn.execute(
                    "INSERT INTO transaction_items (transaction_id, product_barcode, product_name_at_sale, "
                    "unit_price_at_sale, quantity, unit_cost_at_sale, cost_known) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (head["id"], product["barcode"], (line.name or "").strip() or product["name"],
                     _money(line.unit_price), line.quantity, cost, cost > 0))
            for barcode, quantity in wanted.items():
                stored = products[barcode]["barcode"]
                conn.execute("UPDATE stock_levels SET quantity = quantity - %s, updated_at = now() "
                             "WHERE location_kind = %s AND location_code = %s AND product_barcode = %s",
                             (quantity, kind, code, stored))
                conn.execute("UPDATE products SET stock_quantity = stock_quantity - %s, updated_at = now() "
                             "WHERE barcode = %s", (quantity, stored))
            return _read_sale(conn, head["id"])
    except psycopg.errors.UniqueViolation:
        # Two uploads of one sale raced: the other one stored it.
        with db.connect(request.app.state.settings) as conn:
            conn.row_factory = dict_row
            row = conn.execute("SELECT id FROM transactions WHERE client_uuid = %s", (body.client_uuid,)).fetchone()
            return _read_sale(conn, row["id"], duplicate=True)


@router.get("/sales", response_model=list[SaleOut])
def list_sales(request: Request, since: datetime, until: datetime, dealership_code: str | None = None,
               limit: int = Query(default=1000, ge=1, le=5000),
               session: Session = Depends(current_session)) -> list[SaleOut]:
    """Sales in [since, until), newest first; a cashier sees one shop at a time."""
    if session.role not in READERS:
        raise _fail(403, "Not allowed.")
    shop = (dealership_code or "").strip().upper() or None
    if session.role == "cashier" and not shop:
        raise _fail(403, "Pick your dealership.")
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        ids = conn.execute(
            "SELECT id FROM transactions WHERE created_at >= %s AND created_at < %s "
            "AND (%s::text IS NULL OR dealership_code = %s) ORDER BY created_at DESC, id DESC LIMIT %s",
            (since, until, shop, shop, limit)).fetchall()
        return [_read_sale(conn, r["id"]) for r in ids]
