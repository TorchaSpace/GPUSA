"""Products: the list for every signed-in person, and create / update for administrators."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from . import authz, db
from .auth_routes import Session, current_session

router = APIRouter(prefix="/products", tags=["products"])


class ProductOut(BaseModel):
    barcode: str
    name: str
    price: float
    stock_quantity: int
    critical_stock_level: int
    is_active: bool
    cost_price: float | None = None  # only for administrators and depot managers


COST_ROLES = ("admin", "depot_manager")


def _out(row: dict, session: Session) -> ProductOut:
    return ProductOut(
        barcode=row["barcode"], name=row["name"], price=float(row["price"]),
        stock_quantity=row["stock_quantity"], critical_stock_level=row["critical_stock_level"],
        is_active=row["is_active"],
        cost_price=float(row["cost_price"]) if session.role in COST_ROLES else None)


@router.get("", response_model=list[ProductOut])
def list_products(request: Request, include_inactive: bool = False,
                  session: Session = Depends(current_session)) -> list[ProductOut]:
    """Active products by default. ``include_inactive`` is for admins and depot managers."""
    show_all = include_inactive and session.role in ("admin", "depot_manager")
    sql = "SELECT * FROM products" + ("" if show_all else " WHERE is_active") + " ORDER BY name, barcode"
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute(sql).fetchall()
    return [_out(r, session) for r in rows]


@router.get("/{barcode}", response_model=ProductOut)
def get_product(request: Request, barcode: str = Path(max_length=64),
                session: Session = Depends(current_session)) -> ProductOut:
    """One product by barcode in any letter case (switched-off ones too: the caller decides)."""
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        row = conn.execute("SELECT * FROM products WHERE upper(barcode) = %s", (barcode.strip().upper(),)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No product with barcode {barcode}.")
    return _out(row, session)



# --- writes (administrators only, signed in to Admin) -----------------------

ADMINS = frozenset({"admin"})
ADMIN_AREA = frozenset({"admin"})
MAX_AMOUNT = Decimal("1000000000")
MAX_UNITS = 1_000_000
_CENT = Decimal("0.01")


def _money(value: Decimal | None) -> Decimal | None:
    """Half-up to cents, like the desktop (19.999 -> 20.00)."""
    return None if value is None else value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("name must not be blank")
    return value


class ProductCreate(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    price: Decimal = Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    critical_stock_level: int = Field(default=0, ge=0, le=MAX_UNITS)
    cost_price: Decimal = Field(default=Decimal(0), ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    stock_quantity: int = Field(default=0, ge=0, le=MAX_UNITS)
    is_active: bool = True

    @field_validator("barcode")
    @classmethod
    def _barcode(cls, v: str) -> str:
        v = v.strip().upper()  # stored trimmed and upper-case, as on the desktop
        if not v:
            raise ValueError("barcode must not be blank")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v)


class ProductUpdate(BaseModel):
    """Only the fields sent are changed. Stock is never edited here: it moves
    only through /stock-movements (and later sales and shipments), each with
    an audit row."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price: Decimal | None = Field(default=None, ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    critical_stock_level: int | None = Field(default=None, ge=0, le=MAX_UNITS)
    cost_price: Decimal | None = Field(default=None, ge=0, le=MAX_AMOUNT, allow_inf_nan=False)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return _clean_name(v)


_RETURNING = "RETURNING *"


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(body: ProductCreate, request: Request,
                   session: Session = Depends(current_session)) -> ProductOut:
    """A new product. A starting stock is booked as not-yet-placed stock with a
    'receive' movement, so it has an audit trail from day one (desktop rule)."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    actor = authz.actor_label(session)
    try:
        with db.connect(request.app.state.settings) as conn:
            conn.row_factory = dict_row
            authz.require_live(conn, session, roles=ADMINS)
            if conn.execute("SELECT 1 FROM products WHERE upper(barcode) = %s", (body.barcode,)).fetchone():
                raise HTTPException(status_code=409, detail=f"A product with barcode {body.barcode} exists already.")
            row = conn.execute(
                "INSERT INTO products (barcode, name, price, stock_quantity, critical_stock_level, is_active, "
                "cost_price) VALUES (%s, %s, %s, %s, %s, %s, %s) " + _RETURNING,
                (body.barcode, body.name, _money(body.price), body.stock_quantity, body.critical_stock_level,
                 body.is_active, _money(body.cost_price)),
            ).fetchone()
            if body.stock_quantity:
                conn.execute("INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
                             "VALUES ('unassigned', '', %s, %s)", (body.barcode, body.stock_quantity))
                conn.execute(
                    "INSERT INTO stock_movements (product_barcode, movement_type, quantity, note, location_kind, "
                    "location_code, reason, handled_by) VALUES (%s, 'receive', %s, %s, 'unassigned', '', "
                    "'receive', %s)",
                    (body.barcode, body.stock_quantity, "Initial stock entered when the product was created",
                     actor))
            conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) "
                         "VALUES (%s, 'created', 'product', %s, %s)", (actor, body.barcode, body.name))
    except psycopg.errors.UniqueViolation:  # lost a race with another writer
        raise HTTPException(status_code=409, detail=f"A product with barcode {body.barcode} exists already.") from None
    return _out(row, session)


@router.patch("/{barcode}", response_model=ProductOut)
def update_product(body: ProductUpdate, request: Request, barcode: str = Path(max_length=64),
                   session: Session = Depends(current_session)) -> ProductOut:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    changes = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
    for key in ("price", "cost_price"):
        if key in changes:
            changes[key] = _money(changes[key])
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=ADMINS)
        old = conn.execute("SELECT * FROM products WHERE upper(barcode) = %s FOR UPDATE",
                           (barcode.strip().upper(),)).fetchone()
        if old is None:
            raise HTTPException(status_code=404, detail=f"No product with barcode {barcode}.")
        changes = {k: v for k, v in changes.items() if old[k] != v}
        if not changes:
            return _out(old, session)
        columns = sorted(changes)  # fixed names from the model, never user text
        row = conn.execute(
            "UPDATE products SET " + ", ".join(f"{c} = %s" for c in columns) + ", updated_at = now() "
            "WHERE barcode = %s " + _RETURNING,
            (*[changes[c] for c in columns], old["barcode"]),
        ).fetchone()
        detail = "; ".join(f"{c}: {old[c]} -> {changes[c]}" for c in columns)
        conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) "
                     "VALUES (%s, 'updated', 'product', %s, %s)",
                     (authz.actor_label(session), old["barcode"], detail))
    return _out(row, session)
