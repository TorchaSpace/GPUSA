"""Read-only product list for signed-in people (first data endpoint)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from . import db
from .auth_routes import Session, current_session

router = APIRouter(prefix="/products", tags=["products"])


class ProductOut(BaseModel):
    barcode: str
    name: str
    price: float
    stock_quantity: int
    critical_stock_level: int
    is_active: bool


@router.get("", response_model=list[ProductOut])
def list_products(request: Request, include_inactive: bool = False,
                  session: Session = Depends(current_session)) -> list[ProductOut]:
    """Active products by default. ``include_inactive`` is for admins and depot managers."""
    show_all = include_inactive and session.role in ("admin", "depot_manager")
    sql = ("SELECT barcode, name, price, stock_quantity, critical_stock_level, is_active FROM products"
           + ("" if show_all else " WHERE is_active") + " ORDER BY name, barcode")
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute(sql).fetchall()
    return [ProductOut(**{**r, "price": float(r["price"])}) for r in rows]
