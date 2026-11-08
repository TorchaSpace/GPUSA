"""Read-only lists the desktop apps need next to products (all need a token).

Employees carry names, badge numbers and (masked) national IDs, so each
person sees only the staff of the place they manage (administrators: all); the rest is visible to every signed-in account (a till
needs its dealership, shelves and stock).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from . import db
from .auth_routes import Session, current_session

router = APIRouter(tags=["lists"])


def _rows(request: Request, sql: str, params: tuple = ()) -> list[dict]:
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(sql, params).fetchall()


class DealershipOut(BaseModel):
    id: int
    code: str
    name: str
    region: str
    city: str
    manager_name: str | None
    is_active: bool


class WarehouseOut(BaseModel):
    id: int
    code: str
    name: str
    city: str
    capacity_units: int | None
    docks: int
    is_active: bool


class EmployeeOut(BaseModel):
    id: int
    badge_id: str
    name: str
    title: str | None
    role: str
    location_type: str
    location_name: str
    is_active: bool
    national_id_masked: str | None = None


class StockLevelOut(BaseModel):
    location_kind: str
    location_code: str
    product_barcode: str
    product_name: str
    quantity: int
    updated_at: datetime


@router.get("/dealerships", response_model=list[DealershipOut])
def dealerships(request: Request, _: Session = Depends(current_session)):
    return _rows(request, "SELECT id, code, name, region, city, manager_name, is_active "
                          "FROM dealerships ORDER BY code")


@router.get("/warehouses", response_model=list[WarehouseOut])
def warehouses(request: Request, _: Session = Depends(current_session)):
    return _rows(request, "SELECT id, code, name, city, capacity_units, docks, is_active "
                          "FROM warehouses ORDER BY code")


@router.get("/employees", response_model=list[EmployeeOut])
def employees(request: Request, session: Session = Depends(current_session)):
    """Administrators see everyone; a depot manager their warehouse's people, a cashier their shop's
    (they manage them). National IDs only ever leave masked."""
    from .people_routes import MASKED_NATIONAL_ID, at_place, own_place  # people_routes imports this module's peers

    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        place = None if session.role == "admin" else own_place(conn, session)
        rows = conn.execute("SELECT id, badge_id, name, title, role, location_type, location_name, is_active, "
                            f"{MASKED_NATIONAL_ID} FROM employees ORDER BY name, badge_id").fetchall()
    return [r for r in rows if at_place(r["location_type"], r["location_name"], place)]


@router.get("/stock-levels", response_model=list[StockLevelOut])
def stock_levels(request: Request, location_kind: str | None = None, location_code: str | None = None,
                 product_barcode: str | None = None, _: Session = Depends(current_session)):
    """Units per place and product. Optional filters narrow the list."""
    sql = ("SELECT l.location_kind, l.location_code, l.product_barcode, p.name AS product_name, l.quantity, "
           "l.updated_at FROM stock_levels l JOIN products p ON p.barcode = l.product_barcode "
           "WHERE (%s::text IS NULL OR l.location_kind = %s) AND (%s::text IS NULL OR l.location_code = %s) "
           "AND (%s::text IS NULL OR upper(l.product_barcode) = upper(%s)) "
           "ORDER BY p.name, l.location_kind, l.location_code")
    return _rows(request, sql, (location_kind, location_kind, location_code, location_code,
                                product_barcode, product_barcode))
