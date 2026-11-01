"""Stock in and out: POST /stock-movements and the recent-movements list.

Same rules as the desktop's database/stock_repository.py receive()/dispatch():
- 'receive' is new stock arriving from outside the company (a supplier), so
  the place's level AND the company total (products.stock_quantity) go up;
  the product must be active, and a warehouse must be active and have room.
- 'dispatch' is stock leaving the company (damaged, back to the supplier),
  so both go down; never below zero.
Every write leaves one stock_movements row with who did it. Moving stock
between places (transfers, shipments) is not here yet.

Administrators may book at any place. Depot managers, signed in to the depot
console, only at warehouses. Cashiers cannot book stock by hand.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_UNITS

router = APIRouter(prefix="/stock-movements", tags=["stock"])

STOCK_ROLES = frozenset({"admin", "depot_manager"})
STOCK_AREAS = frozenset({"admin", "depot_console"})
_TABLE_FOR_KIND = {"warehouse": "warehouses", "dealership": "dealerships"}


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


class MovementIn(BaseModel):
    direction: Literal["receive", "dispatch"]
    barcode: str = Field(min_length=1, max_length=64)
    location_kind: Literal["warehouse", "dealership", "unassigned"]
    location_code: str = Field(default="", max_length=32)
    quantity: int = Field(gt=0, le=MAX_UNITS)
    note: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    bin_code: str | None = Field(default=None, max_length=32)


class MovementOut(BaseModel):
    id: int
    created_at: datetime
    direction: str
    barcode: str
    location_kind: str
    location_code: str
    quantity: int
    level_after: int | None = None
    note: str | None
    reference: str | None
    handled_by: str | None


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _check_place(conn, kind: str, code: str, direction: str, quantity: int) -> None:
    """404 for an unknown place; for stock coming into a warehouse, 409 if it
    is switched off or the units would not fit (no capacity = no limit)."""
    table = _TABLE_FOR_KIND.get(kind)
    if table is None:
        return
    # Lock the place so two receipts can't both squeeze into the last free room.
    place = conn.execute(f"SELECT * FROM {table} WHERE code = %s FOR UPDATE", (code,)).fetchone()
    if place is None:
        raise _fail(404, f"No {kind} with code {code}.")
    if kind != "warehouse" or direction != "receive":
        return
    if not place["is_active"]:
        raise _fail(409, f"Warehouse {code} is switched off.")
    if place["capacity_units"] is not None:
        used = conn.execute("SELECT COALESCE(SUM(quantity), 0) AS used FROM stock_levels "
                            "WHERE location_kind = 'warehouse' AND location_code = %s", (code,)).fetchone()["used"]
        if used + quantity > place["capacity_units"]:
            raise _fail(409, f"Warehouse {code} holds {used} of {place['capacity_units']} units; "
                             f"{quantity} more do not fit.")


@router.post("", response_model=MovementOut, status_code=status.HTTP_201_CREATED)
def book_movement(body: MovementIn, request: Request, session: Session = Depends(current_session)) -> MovementOut:
    authz.require(session, roles=STOCK_ROLES, areas=STOCK_AREAS)
    kind = body.location_kind
    code = "" if kind == "unassigned" else body.location_code.strip().upper()
    if kind != "unassigned" and not code:
        raise _fail(422, "location_code is required for a warehouse or dealership.")
    if session.role == "depot_manager" and kind != "warehouse":
        raise _fail(403, "Depot managers book stock at warehouses only.")
    sign = 1 if body.direction == "receive" else -1

    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=STOCK_ROLES)
        # Lock the product first: every change to its levels and total goes through this row.
        product = conn.execute("SELECT barcode, is_active FROM products WHERE upper(barcode) = %s FOR UPDATE",
                               (body.barcode.strip().upper(),)).fetchone()
        if product is None:
            raise _fail(404, f"No product with barcode {body.barcode}.")
        if body.direction == "receive" and not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off; it cannot receive stock.")
        _check_place(conn, kind, code, body.direction, body.quantity)
        barcode = product["barcode"]
        current = conn.execute(
            "SELECT quantity FROM stock_levels WHERE location_kind = %s AND location_code = %s "
            "AND product_barcode = %s", (kind, code, barcode)).fetchone()
        current = current["quantity"] if current else 0
        new = current + sign * body.quantity
        if new < 0:
            raise _fail(409, f"Only {current} units of {barcode} at this place; cannot dispatch {body.quantity}.")
        conn.execute(
            "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (location_kind, location_code, product_barcode) "
            "DO UPDATE SET quantity = excluded.quantity, updated_at = now()", (kind, code, barcode, new))
        conn.execute("UPDATE products SET stock_quantity = stock_quantity + %s, updated_at = now() "
                     "WHERE barcode = %s", (sign * body.quantity, barcode))
        row = conn.execute(
            "INSERT INTO stock_movements (product_barcode, movement_type, quantity, note, location_kind, "
            "location_code, reason, reference, handled_by, bin_code) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (barcode, body.direction, body.quantity, _clean(body.note), kind, code, body.direction,
             _clean(body.reference), authz.actor_label(session), _clean(body.bin_code)),
        ).fetchone()
    return _movement(row, level_after=new)


def _movement(row: dict, level_after: int | None = None) -> MovementOut:
    return MovementOut(id=row["id"], created_at=row["created_at"], direction=row["movement_type"],
                       barcode=row["product_barcode"], location_kind=row["location_kind"] or "",
                       location_code=row["location_code"] or "", quantity=row["quantity"],
                       level_after=level_after, note=row["note"], reference=row["reference"],
                       handled_by=row["handled_by"])


@router.get("", response_model=list[MovementOut])
def recent_movements(request: Request, product_barcode: str | None = None,
                     limit: int = Query(default=100, ge=1, le=500),
                     session: Session = Depends(current_session)) -> list[MovementOut]:
    """Newest first. Administrators and depot managers only (it shows who did what)."""
    if session.role not in STOCK_ROLES:
        raise _fail(403, "Only administrators and depot managers may list stock movements.")
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute(
            "SELECT * FROM stock_movements WHERE (%s::text IS NULL OR upper(product_barcode) = upper(%s)) "
            "ORDER BY created_at DESC, id DESC LIMIT %s", (product_barcode, product_barcode, limit)).fetchall()
    return [_movement(r) for r in rows]
