"""Dealerships, warehouses and products: create / edit / delete, plus store settings and the admin audit trail.
Same rules as the desktop repositories:

- Codes are stored trimmed and upper-case; a duplicate code (any letter case) is a 409.
- A dealership or warehouse that still holds stock, has a scheduled / in-transit shipment (warehouses: also an
  open purchase order) cannot be deleted - deactivate it instead. A capacity below the units a warehouse holds
  now is refused. Deleting a dealership cancels its open stock requests; a product that was ever used is kept.
- Every delete is written to ``admin_audit`` in the same transaction. Only administrators (admin area) write.

Settings are a small key/value store. Language, currency, store name/address and the alert switches are public
(a till needs them before anyone signs in); read-cursors for the notification bell are administrators only.
Recovery secrets live in the same table but are never served.
"""

from __future__ import annotations

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import authz, db
from .auth_routes import Session, current_session
from .stock_routes import _fail, _write

router = APIRouter(tags=["places"])

ADMINS = frozenset({"admin"})
ADMIN_AREA = frozenset({"admin"})
PUBLIC_SETTINGS = frozenset({"store.name", "store.address", "alerts.low_stock", "alerts.pending_approvals",
                             "ui.language", "ui.currency"})
PRIVATE_PREFIX = "notifications.read."


def _admin(session: Session) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)


def _code(text: str) -> str:
    return (text or "").strip().upper()


def _audit(conn, session: Session, action: str, kind: str, target: str, detail: str | None = None) -> None:
    conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) VALUES (%s, %s, %s, %s, %s)",
                 (authz.actor_label(session), action, kind, target, detail))


def _held(conn, kind: str, code: str) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(quantity), 0) AS n FROM stock_levels "
                            "WHERE location_kind = %s AND location_code = %s", (kind, code)).fetchone()["n"])


def _drop_levels(conn, kind: str, code: str) -> None:
    conn.execute("DELETE FROM stock_levels WHERE location_kind = %s AND location_code = %s", (kind, code))
    conn.execute("DELETE FROM location_reorder_levels WHERE location_kind = %s AND location_code = %s", (kind, code))


def _no_stock(conn, kind: str, code: str) -> None:
    units = _held(conn, kind, code)
    if units:
        raise _fail(409, f"{code} still holds {units} units - move them elsewhere before deleting it.")


# --- dealerships ----------------------------------------------------------------------

class DealershipIn(BaseModel):
    code: str = Field(max_length=100)
    name: str = Field(max_length=200)
    region: Literal["Metro", "Coastal", "Valley"]
    city: str = Field(default="", max_length=200)
    manager_name: str | None = Field(default=None, max_length=200)
    is_active: bool = True


def _dealership(body: DealershipIn) -> dict:
    f = {"code": _code(body.code), "name": " ".join(body.name.split()), "region": body.region,
         "city": " ".join(body.city.split()), "manager_name": " ".join((body.manager_name or "").split()) or None,
         "is_active": body.is_active}
    if not f["code"]:
        raise _fail(422, "A dealership code is required.")
    if len(f["code"]) > 24:
        raise _fail(422, "A code can be at most 24 characters.")
    if not f["name"]:
        raise _fail(422, "A dealership name is required.")
    if len(f["name"]) > 120 or len(f["city"]) > 120 or len(f["manager_name"] or "") > 120:
        raise _fail(422, "Name, city and manager can be at most 120 characters.")
    return f


def _find(conn, table: str, code: str, lock: bool = False):
    raw = (code or "").strip()
    return conn.execute(f"SELECT * FROM {table} WHERE code = %s OR upper(code) = %s "  # noqa: S608 - fixed names
                        "ORDER BY (code = %s) DESC, id LIMIT 1" + (" FOR UPDATE" if lock else ""),
                        (raw, raw.upper(), raw.upper())).fetchone()


@router.post("/dealerships", status_code=201)
def create_dealership(body: DealershipIn, request: Request, session: Session = Depends(current_session)) -> dict:
    _admin(session)
    f = _dealership(body)

    def work(conn):
        if _find(conn, "dealerships", f["code"]) is not None:
            raise _fail(409, f"Dealership {f['code']} already exists.")
        try:
            row = conn.execute(
                "INSERT INTO dealerships (code, name, region, city, manager_name, is_active) "
                "VALUES (%(code)s, %(name)s, %(region)s, %(city)s, %(manager_name)s, %(is_active)s) "
                "RETURNING id, code, name, region, city, manager_name, is_active", f).fetchone()
        except psycopg.errors.UniqueViolation:
            raise _fail(409, f"Dealership {f['code']} already exists.") from None
        return row

    return _write(request, session, ADMINS, work)


@router.put("/dealerships/{code}")
def update_dealership(body: DealershipIn, request: Request, code: str = Path(max_length=100),
                      session: Session = Depends(current_session)) -> dict:
    """The code is fixed; the body's code is ignored in favour of the path."""
    _admin(session)
    f = _dealership(body.model_copy(update={"code": code}))

    def work(conn):
        old = _find(conn, "dealerships", code, lock=True)
        if old is None:
            raise _fail(404, f"No dealership {f['code']}.")
        return conn.execute(
            "UPDATE dealerships SET name = %(name)s, region = %(region)s, city = %(city)s, "
            "manager_name = %(manager_name)s, is_active = %(is_active)s, updated_at = now() WHERE id = %(id)s "
            "RETURNING id, code, name, region, city, manager_name, is_active", {**f, "id": old["id"]}).fetchone()

    return _write(request, session, ADMINS, work)


@router.delete("/dealerships/{code}", status_code=204)
def delete_dealership(request: Request, code: str = Path(max_length=100),
                      session: Session = Depends(current_session)) -> None:
    _admin(session)

    def work(conn):
        row = _find(conn, "dealerships", code, lock=True)
        if row is None:
            raise _fail(404, f"No dealership {_code(code)}.")
        stored = row["code"]
        _no_stock(conn, "dealership", stored)
        open_shipments = conn.execute(
            "SELECT COUNT(*) AS n FROM shipments WHERE status IN ('scheduled', 'in_transit') AND dealership_code = %s",
            (stored,)).fetchone()["n"]
        if open_shipments:
            raise _fail(409, f"Dealership {stored} has {open_shipments} active shipments; deactivate it instead.")
        conn.execute("DELETE FROM dealerships WHERE id = %s", (row["id"],))
        _drop_levels(conn, "dealership", stored)
        conn.execute("UPDATE stock_requests SET status = 'cancelled', decided_by = %s, "
                     "decision_note = 'Dealership removed', updated_at = now() "
                     "WHERE dealership_code = %s AND status = 'open'", (authz.actor_label(session), stored))
        _audit(conn, session, "deleted", "dealership", stored, row["name"])

    _write(request, session, ADMINS, work)


# --- warehouses -----------------------------------------------------------------------

class WarehouseIn(BaseModel):
    code: str = Field(max_length=100)
    name: str = Field(max_length=300)
    city: str = Field(default="", max_length=300)
    capacity_units: int | None = Field(default=None, gt=0, le=2_000_000_000)
    docks: int = Field(default=0, ge=0, le=100_000)
    is_active: bool = True


def _warehouse(body: WarehouseIn) -> dict:
    f = {"code": _code(body.code), "name": body.name.strip(), "city": body.city.strip(),
         "capacity_units": body.capacity_units, "docks": body.docks, "is_active": body.is_active}
    if not f["code"]:
        raise _fail(422, "A warehouse code is required.")
    if not f["name"]:
        raise _fail(422, "A warehouse name is required.")
    return f


_WAREHOUSE = "RETURNING id, code, name, city, capacity_units, docks, is_active"


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn, request: Request, session: Session = Depends(current_session)) -> dict:
    _admin(session)
    f = _warehouse(body)

    def work(conn):
        if conn.execute("SELECT 1 FROM warehouses WHERE upper(code) = %s", (f["code"],)).fetchone():
            raise _fail(409, f"Warehouse {f['code']} already exists.")
        try:
            return conn.execute(
                "INSERT INTO warehouses (code, name, city, capacity_units, docks, is_active) "
                "VALUES (%(code)s, %(name)s, %(city)s, %(capacity_units)s, %(docks)s, %(is_active)s) " + _WAREHOUSE,
                f).fetchone()
        except psycopg.errors.UniqueViolation:
            raise _fail(409, f"Warehouse {f['code']} already exists.") from None

    return _write(request, session, ADMINS, work)


@router.put("/warehouses/{code}")
def update_warehouse(body: WarehouseIn, request: Request, code: str = Path(max_length=100),
                     session: Session = Depends(current_session)) -> dict:
    _admin(session)
    f = _warehouse(body.model_copy(update={"code": code}))

    def work(conn):
        old = _find(conn, "warehouses", code, lock=True)
        if old is None:
            raise _fail(404, f"No warehouse {f['code']}.")
        if f["capacity_units"] is not None:
            used = _held(conn, "warehouse", old["code"])
            if used > f["capacity_units"]:
                raise _fail(409, f"Warehouse {old['code']} holds {used} units, over the capacity of "
                                 f"{f['capacity_units']}. Move stock out first.")
        return conn.execute(
            "UPDATE warehouses SET name = %(name)s, city = %(city)s, capacity_units = %(capacity_units)s, "
            "docks = %(docks)s, is_active = %(is_active)s, updated_at = now() WHERE id = %(id)s " + _WAREHOUSE,
            {**f, "id": old["id"]}).fetchone()

    return _write(request, session, ADMINS, work)


@router.delete("/warehouses/{code}", status_code=204)
def delete_warehouse(request: Request, code: str = Path(max_length=100),
                     session: Session = Depends(current_session)) -> None:
    _admin(session)

    def work(conn):
        row = _find(conn, "warehouses", code, lock=True)
        if row is None:
            raise _fail(404, f"No warehouse {_code(code)}.")
        stored = row["code"]
        _no_stock(conn, "warehouse", stored)
        open_shipments = conn.execute(
            "SELECT COUNT(*) AS n FROM shipments WHERE status IN ('scheduled', 'in_transit') AND origin_code = %s",
            (stored,)).fetchone()["n"]
        if open_shipments:
            raise _fail(409, f"Warehouse {stored} has {open_shipments} active shipments; deactivate it instead.")
        orders = conn.execute(
            "SELECT COUNT(*) AS n FROM purchase_orders WHERE status IN ('pending', 'sent', 'partially_received') "
            "AND upper(btrim(split_part(site, '·', 1))) = %s", (stored.upper(),)).fetchone()["n"]
        if orders:
            raise _fail(409, f"Warehouse {stored} has {orders} open purchase orders; finish or cancel them first.")
        conn.execute("DELETE FROM warehouses WHERE id = %s", (row["id"],))
        _drop_levels(conn, "warehouse", stored)
        _audit(conn, session, "deleted", "warehouse", stored)

    _write(request, session, ADMINS, work)


# --- products -------------------------------------------------------------------------

@router.delete("/products/{barcode}", status_code=204)
def delete_product(request: Request, barcode: str = Path(max_length=64),
                   session: Session = Depends(current_session)) -> None:
    """Only a product that was never used; anything with stock or history is deactivated instead."""
    _admin(session)

    def work(conn):
        row = conn.execute("SELECT * FROM products WHERE upper(barcode) = %s FOR UPDATE",
                           (barcode.strip().upper(),)).fetchone()
        if row is None:
            raise _fail(404, f"No product with barcode {barcode}.")
        code = row["barcode"]
        held = _held_product(conn, code)
        if held > 0 or row["stock_quantity"] > 0:
            raise _fail(409, f"{code} can't be deleted: {max(held, row['stock_quantity'])} units are still in stock. "
                             "Deactivate it instead.")
        for table, what in (("stock_movements", "it has stock movements on record"),
                            ("transaction_items", "it appears on past sales"),
                            ("shipment_lines", "it appears on shipments"),
                            ("purchase_orders", "it has purchase orders")):
            if conn.execute(f"SELECT 1 FROM {table} WHERE product_barcode = %s LIMIT 1", (code,)).fetchone():  # noqa: S608
                raise _fail(409, f"{code} can't be deleted: {what}. Deactivate it instead.")
        conn.execute("UPDATE stock_requests SET status = 'cancelled', decided_by = %s, "
                     "decision_note = 'Product removed', updated_at = now() "
                     "WHERE upper(product_barcode) = %s AND status = 'open'",
                     (authz.actor_label(session), code.upper()))
        conn.execute("DELETE FROM products WHERE barcode = %s", (code,))
        _audit(conn, session, "deleted", "product", code, row["name"])

    _write(request, session, ADMINS, work)


def _held_product(conn, barcode: str) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(quantity), 0) AS n FROM stock_levels WHERE product_barcode = %s",
                            (barcode,)).fetchone()["n"])


# --- settings -------------------------------------------------------------------------

class SettingsIn(BaseModel):
    values: dict[str, str] = Field(max_length=50)


@router.get("/settings")
def public_settings(request: Request) -> dict[str, str]:
    """Language, currency, store profile and alert switches - no token needed (a till reads them before login)."""
    with db.connect(request.app.state.settings) as conn:
        rows = conn.execute("SELECT key, value FROM app_settings WHERE key = ANY(%s)", (sorted(PUBLIC_SETTINGS),))
        return {key: value for key, value in rows.fetchall()}


@router.get("/settings/private")
def private_settings(request: Request, session: Session = Depends(current_session)) -> dict[str, str]:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        rows = conn.execute("SELECT key, value FROM app_settings WHERE key LIKE %s", (PRIVATE_PREFIX + "%",))
        return {key: value for key, value in rows.fetchall()}


@router.put("/settings")
def save_settings(body: SettingsIn, request: Request, session: Session = Depends(current_session)) -> dict[str, str]:
    _admin(session)
    for key, value in body.values.items():
        if key not in PUBLIC_SETTINGS and not key.startswith(PRIVATE_PREFIX):
            raise _fail(422, f"Unknown setting {key}.")
        if len(value) > 2000 or len(key) > 100:
            raise _fail(422, "A setting is too long.")

    def work(conn):
        for key, value in body.values.items():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (key, value))
        return body.values

    return _write(request, session, ADMINS, work)


# --- audit trail ----------------------------------------------------------------------

class AuditEntryOut(BaseModel):
    id: int
    at: str
    actor: str | None
    action: str
    kind: str
    target: str
    detail: str | None


@router.get("/audit", response_model=list[AuditEntryOut])
def audit(request: Request, limit: int = Query(default=100, ge=0, le=1000), kind: str | None = None,
          session: Session = Depends(current_session)) -> list[AuditEntryOut]:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute("SELECT id, at, actor, action, kind, target, detail FROM admin_audit "
                            "WHERE (%s::text IS NULL OR kind = %s) ORDER BY id DESC LIMIT %s",
                            (kind, kind, limit)).fetchall()
    return [AuditEntryOut(**{**r, "at": r["at"].isoformat()}) for r in rows]
