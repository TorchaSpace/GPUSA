"""Read-only lists the desktop apps need next to products (all need a token).

Employees carry names and badge numbers, so only administrators and depot
managers may list them; the rest is visible to every signed-in account (a till
needs its dealership, shelves and stock).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
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


class StockLevelOut(BaseModel):
    location_kind: str
    location_code: str
    product_barcode: str
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
    if session.role not in ("admin", "depot_manager"):
        raise HTTPException(status_code=403, detail="Only administrators and depot managers may list employees.")
    return _rows(request, "SELECT id, badge_id, name, title, role, location_type, location_name, is_active "
                          "FROM employees ORDER BY name, badge_id")


@router.get("/stock-levels", response_model=list[StockLevelOut])
def stock_levels(request: Request, location_kind: str | None = None, location_code: str | None = None,
                 product_barcode: str | None = None, _: Session = Depends(current_session)):
    """Units per place and product. Optional filters narrow the list."""
    sql = ("SELECT location_kind, location_code, product_barcode, quantity, updated_at FROM stock_levels "
           "WHERE (%s::text IS NULL OR location_kind = %s) AND (%s::text IS NULL OR location_code = %s) "
           "AND (%s::text IS NULL OR upper(product_barcode) = upper(%s)) "
           "ORDER BY location_kind, location_code, product_barcode")
    return _rows(request, sql, (location_kind, location_kind, location_code, location_code,
                                product_barcode, product_barcode))
