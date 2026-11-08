"""Stock per place: in/out, transfers, counts, reorder levels and the reads the
desktop screens need. Same rules as the desktop's database/stock_repository.py:

- Every unit sits at one place (a warehouse, a dealership, or "unassigned" =
  not placed yet) or on a truck. products.stock_quantity is the company total.
- Stock entering or leaving the company (receive, dispatch, a count
  correction) moves a level AND the total; stock moving between places
  (transfers) moves levels only.
- A level never goes below zero; stock coming into a warehouse needs it to be
  switched on and to have room; every change leaves stock_movements rows with
  who did it.

Who: administrators (signed in to Admin, or the depot console) anywhere;
depot managers (depot console) at warehouses only; cashiers read only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import activity, authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_UNITS

router = APIRouter(tags=["stock"])

STOCK_ROLES = frozenset({"admin", "depot_manager"})
STOCK_AREAS = frozenset({"admin", "depot_console"})
ADMINS = frozenset({"admin", "depot_manager", "cashier"})  # Admin area: root + IAM users
ADMIN_AREA = frozenset({"admin"})
_TABLE_FOR_KIND = {"warehouse": "warehouses", "dealership": "dealerships"}
Kind = Literal["warehouse", "dealership", "unassigned"]


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


class Place(BaseModel):
    kind: Kind
    code: str = Field(default="", max_length=32)

    def key(self) -> tuple[str, str]:
        """(kind, code) as stored: code upper-case, '' for unassigned. 422 if a code is missing."""
        code = "" if self.kind == "unassigned" else self.code.strip().upper()
        if self.kind != "unassigned" and not code:
            raise _fail(422, "A warehouse or dealership needs its code.")
        return self.kind, code

    @staticmethod
    def label(kind: str, code: str) -> str:
        return "Unassigned" if kind == "unassigned" else code


# --- the core, inside one transaction ----------------------------------------

def _allowed_place(session: Session, kind: str) -> None:
    if session.role == "depot_manager" and kind != "warehouse":
        raise _fail(403, "Depot managers work at warehouses only.")


def _lock_product(conn, barcode: str) -> dict:
    """The product row (any letter case), locked: every change to its levels and
    total goes through it, so concurrent writes on one product queue up."""
    row = conn.execute("SELECT barcode, name, is_active FROM products WHERE upper(barcode) = %s FOR UPDATE",
                       (barcode.strip().upper(),)).fetchone()
    if row is None:
        raise _fail(404, f"No product with barcode {barcode}.")
    return row


def _require_place(conn, kind: str, code: str) -> dict | None:
    table = _TABLE_FOR_KIND.get(kind)
    if table is None:
        return None
    place = conn.execute(f"SELECT * FROM {table} WHERE code = %s FOR UPDATE", (code,)).fetchone()
    if place is None:
        raise _fail(404, f"No {kind} with code {code}.")
    return place


def _require_can_hold(conn, kind: str, code: str, units: int) -> None:
    """Inbound stock: a warehouse must be switched on and have room (no capacity = no limit)."""
    if kind != "warehouse":
        return
    place = _require_place(conn, kind, code)
    if not place["is_active"]:
        raise _fail(409, f"Warehouse {code} is switched off.")
    if place["capacity_units"] is not None:
        used = _units_at(conn, kind, code)
        if used + units > place["capacity_units"]:
            raise _fail(409, f"Warehouse {code} holds {used} of {place['capacity_units']} units; "
                             f"{units} more do not fit.")


def _units_at(conn, kind: str, code: str) -> int:
    return conn.execute("SELECT COALESCE(SUM(quantity), 0) AS n FROM stock_levels "
                        "WHERE location_kind = %s AND location_code = %s", (kind, code)).fetchone()["n"]


def _level(conn, kind: str, code: str, barcode: str) -> int:
    row = conn.execute("SELECT quantity FROM stock_levels WHERE location_kind = %s AND location_code = %s "
                       "AND product_barcode = %s", (kind, code, barcode)).fetchone()
    return row["quantity"] if row else 0


def _change(conn, kind: str, code: str, barcode: str, delta: int, *, change_total: bool,
            alert: bool = False, actor: str | None = None) -> int:
    """Add delta (may be negative) to one level; 409 before writing if it would go below zero. With `alert`
    a drop that crosses the reorder level (or empties the shelf) lands in the bell, under `actor`."""
    current = _level(conn, kind, code, barcode)
    new = current + delta
    if new < 0:
        raise _fail(409, f"Only {current} units of {barcode} at {Place.label(kind, code)}; "
                         f"{-delta} cannot leave.")
    conn.execute(
        "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (location_kind, location_code, product_barcode) "
        "DO UPDATE SET quantity = excluded.quantity, updated_at = now()", (kind, code, barcode, new))
    if change_total and delta:
        conn.execute("UPDATE products SET stock_quantity = stock_quantity + %s, updated_at = now() "
                     "WHERE barcode = %s", (delta, barcode))
    if alert and delta < 0:
        activity.stock_crossing(conn, kind, code, barcode, current, new, actor=actor)
    return new


def _log(conn, kind: str, code: str, barcode: str, direction: str, quantity: int, *, reason: str,
         actor: str, note: str | None = None, reference: str | None = None, bin_code: str | None = None) -> dict:
    return conn.execute(
        "INSERT INTO stock_movements (product_barcode, movement_type, quantity, note, location_kind, "
        "location_code, reason, reference, handled_by, bin_code) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (barcode, direction, quantity, note, kind, code, reason, reference, actor, bin_code)).fetchone()


def _write(request: Request, session: Session, roles: frozenset[str], work):
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=roles)
        return work(conn)


# --- in / out ------------------------------------------------------------------

class MovementIn(BaseModel):
    direction: Literal["receive", "dispatch"]
    barcode: str = Field(min_length=1, max_length=64)
    location_kind: Kind
    location_code: str = Field(default="", max_length=32)
    quantity: int = Field(gt=0, le=MAX_UNITS)
    note: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    bin_code: str | None = Field(default=None, max_length=32)
    # The depot Floor: the person at the shelf typed their badge, the movement is credited to them
    # (the signed-in console account stays the one that is allowed to write it).
    operator_badge: str | None = Field(default=None, max_length=32)


class MovementOut(BaseModel):
    id: int
    created_at: datetime
    direction: str
    barcode: str
    product_name: str | None = None
    location_kind: str
    location_code: str
    quantity: int
    level_after: int | None = None
    note: str | None
    reason: str | None = None
    reference: str | None
    handled_by: str | None
    bin_code: str | None = None


def _movement(row: dict, level_after: int | None = None) -> MovementOut:
    return MovementOut(id=row["id"], created_at=row["created_at"], direction=row["movement_type"],
                       barcode=row["product_barcode"], product_name=row.get("product_name"),
                       location_kind=row["location_kind"] or "", location_code=row["location_code"] or "",
                       quantity=row["quantity"], level_after=level_after, note=row["note"], reason=row["reason"],
                       reference=row["reference"], handled_by=row["handled_by"], bin_code=row["bin_code"])


@router.post("/stock-movements", response_model=MovementOut, status_code=status.HTTP_201_CREATED)
def book_movement(body: MovementIn, request: Request, session: Session = Depends(current_session)) -> MovementOut:
    """Stock arriving from outside the company (receive) or leaving it (dispatch)."""
    authz.require(session, roles=STOCK_ROLES, areas=STOCK_AREAS)
    kind, code = Place(kind=body.location_kind, code=body.location_code).key()
    _allowed_place(session, kind)
    sign = 1 if body.direction == "receive" else -1

    def work(conn):
        product = _lock_product(conn, body.barcode)
        if body.direction == "receive" and not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off; it cannot receive stock.")
        if kind == "warehouse":
            authz.scope_warehouse(conn, session, code)   # a depot manager books only at their own warehouse
        _require_place(conn, kind, code)
        if body.direction == "receive":
            _require_can_hold(conn, kind, code, body.quantity)
        actor = authz.actor_label(session)
        if _clean(body.operator_badge):
            from .people_routes import operator_label  # people_routes imports this module

            actor = operator_label(conn, body.operator_badge, code if kind == "warehouse" else None)
        new = _change(conn, kind, code, product["barcode"], sign * body.quantity, change_total=True,
                      alert=True, actor=actor)
        row = _log(conn, kind, code, product["barcode"], body.direction, body.quantity, reason=body.direction,
                   actor=actor, note=_clean(body.note), reference=_clean(body.reference),
                   bin_code=_clean(body.bin_code))
        return _movement(row, level_after=new)

    return _write(request, session, STOCK_ROLES, work)


# --- transfers between places (company total unchanged) ------------------------

class Leg(Place):
    quantity: int = Field(gt=0, le=MAX_UNITS)


class TransferIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    source: Place
    legs: list[Leg] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)


@router.post("/stock/transfers", status_code=status.HTTP_201_CREATED)
def transfer(body: TransferIn, request: Request, session: Session = Depends(current_session)) -> dict:
    """Move one product from `source` to one or more places, all or nothing
    (desktop transfer() and distribute()). Administrators only."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    src = body.source.key()
    legs = [(leg.key(), leg.quantity) for leg in body.legs]
    places = [p for p, _ in legs]
    if len(set(places)) != len(places) or src in places:
        raise _fail(422, "Pick two different places.")
    actor = authz.actor_label(session)
    note = _clean(body.note)

    def work(conn):
        product = _lock_product(conn, body.barcode)
        barcode = product["barcode"]
        _require_place(conn, *src)
        moved = 0
        for (kind, code), qty in legs:
            _require_place(conn, kind, code)
            _require_can_hold(conn, kind, code, qty)
            _change(conn, *src, barcode, -qty, change_total=False, alert=True, actor=actor)
            _change(conn, kind, code, barcode, qty, change_total=False)
            _log(conn, *src, barcode, "dispatch", qty, reason="transfer", actor=actor, note=note,
                 reference=Place.label(kind, code))
            _log(conn, kind, code, barcode, "receive", qty, reason="transfer", actor=actor, note=note,
                 reference=Place.label(*src))
            moved += qty
        return {"moved": moved, "source_level_after": _level(conn, *src, barcode)}

    return _write(request, session, ADMINS, work)


# --- stock counts --------------------------------------------------------------

class CountIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    place: Place
    counted: int = Field(ge=0, le=MAX_UNITS)
    note: str | None = Field(default=None, max_length=500)


@router.post("/stock/counts")
def count(body: CountIn, request: Request, session: Session = Depends(current_session)) -> dict:
    """Someone counted `counted` units: the level becomes that and the difference
    is a 'count' movement that moves the company total, except units that were
    waiting unplaced, which are simply placed here. Returns the difference."""
    authz.require(session, roles=STOCK_ROLES, areas=STOCK_AREAS)
    kind, code = body.place.key()
    _allowed_place(session, kind)

    def work(conn):
        barcode = _lock_product(conn, body.barcode)["barcode"]
        if kind == "warehouse":
            authz.scope_warehouse(conn, session, code)
        _require_place(conn, kind, code)
        diff = body.counted - _level(conn, kind, code, barcode)
        if diff:
            absorbed = min(diff, _level(conn, "unassigned", "", barcode)) if diff > 0 and kind != "unassigned" else 0
            if absorbed:
                _change(conn, "unassigned", "", barcode, -absorbed, change_total=False)
                _change(conn, kind, code, barcode, absorbed, change_total=False)
            if diff - absorbed:
                _change(conn, kind, code, barcode, diff - absorbed, change_total=True, alert=True,
                        actor=authz.actor_label(session))
            _log(conn, kind, code, barcode, "receive" if diff > 0 else "dispatch", abs(diff), reason="count",
                 actor=authz.actor_label(session), note=_clean(body.note) or f"Counted {body.counted}")
        return {"difference": diff}

    return _write(request, session, STOCK_ROLES, work)


# --- not-yet-placed stock ------------------------------------------------------

@router.post("/stock/unassigned/clear")
def clear_unassigned(request: Request, session: Session = Depends(current_session)) -> dict:
    """Write off every unplaced unit (it was also counted at a warehouse)."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        removed = 0
        for row in conn.execute("SELECT product_barcode AS b, quantity FROM stock_levels "
                                "WHERE location_kind = 'unassigned' AND quantity > 0 FOR UPDATE").fetchall():
            _change(conn, "unassigned", "", row["b"], -row["quantity"], change_total=True)
            _log(conn, "unassigned", "", row["b"], "dispatch", row["quantity"], reason="count",
                 actor=authz.actor_label(session), note="Cleared unplaced stock (already counted at a warehouse)")
            removed += row["quantity"]
        return {"removed": removed}

    return _write(request, session, ADMINS, work)


@router.post("/stock/unassigned/place")
def place_unassigned(body: Place, request: Request, session: Session = Depends(current_session)) -> dict:
    """Move every unplaced unit of every product to one place."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    kind, code = body.key()
    if kind == "unassigned":
        raise _fail(422, "Pick a warehouse or dealership.")
    actor = authz.actor_label(session)

    def work(conn):
        _require_place(conn, kind, code)
        rows = conn.execute("SELECT product_barcode AS b, quantity FROM stock_levels "
                            "WHERE location_kind = 'unassigned' AND quantity > 0 FOR UPDATE").fetchall()
        _require_can_hold(conn, kind, code, sum(r["quantity"] for r in rows))
        for r in rows:
            _change(conn, "unassigned", "", r["b"], -r["quantity"], change_total=False)
            _change(conn, kind, code, r["b"], r["quantity"], change_total=False)
            _log(conn, "unassigned", "", r["b"], "dispatch", r["quantity"], reason="transfer", actor=actor,
                 reference=Place.label(kind, code), note="Placed unassigned stock")
            _log(conn, kind, code, r["b"], "receive", r["quantity"], reason="transfer", actor=actor,
                 reference="Unassigned", note="Placed unassigned stock")
        return {"moved": sum(r["quantity"] for r in rows)}

    return _write(request, session, ADMINS, work)


# --- reorder levels per place --------------------------------------------------

class ReorderIn(BaseModel):
    place: Place
    barcode: str = Field(min_length=1, max_length=64)
    level: int | None = Field(default=None, ge=0, le=MAX_UNITS)  # None = back to the product's own


@router.put("/stock/reorder-levels")
def set_reorder_level(body: ReorderIn, request: Request, session: Session = Depends(current_session)) -> dict:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    kind, code = body.place.key()
    if kind == "unassigned":
        raise _fail(422, "Pick a warehouse or dealership.")

    def work(conn):
        _require_place(conn, kind, code)
        barcode = _lock_product(conn, body.barcode)["barcode"]
        if body.level is None:
            conn.execute("DELETE FROM location_reorder_levels WHERE location_kind = %s AND location_code = %s "
                         "AND product_barcode = %s", (kind, code, barcode))
        else:
            conn.execute(
                "INSERT INTO location_reorder_levels (location_kind, location_code, product_barcode, level) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (location_kind, location_code, product_barcode) "
                "DO UPDATE SET level = excluded.level, updated_at = now()", (kind, code, barcode, body.level))
        return {"barcode": barcode, "level": body.level}

    return _write(request, session, ADMINS, work)


# --- reads -----------------------------------------------------------------------

def _rows(request: Request, sql: str, params: tuple = ()) -> list[dict]:
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(sql, params).fetchall()


def _place_q(kind: Kind, code: str) -> tuple[str, str]:
    return Place(kind=kind, code=code).key()


@router.get("/stock/reorder-levels")
def reorder_levels(request: Request, kind: Kind, code: str = "", _: Session = Depends(current_session)) -> dict:
    """{barcode: level} for the products this place has its own reorder level for."""
    k, c = _place_q(kind, code)
    rows = _rows(request, "SELECT product_barcode, level FROM location_reorder_levels "
                          "WHERE location_kind = %s AND location_code = %s", (k, c))
    return {r["product_barcode"]: r["level"] for r in rows}


class ProductHere(BaseModel):
    barcode: str
    name: str
    price: float
    quantity: int
    critical_stock_level: int
    is_active: bool
    stocked_here: bool


@router.get("/stock/products-at", response_model=list[ProductHere])
def products_at(request: Request, kind: Kind, code: str = "", include_inactive: bool = False,
                _: Session = Depends(current_session)) -> list[ProductHere]:
    """Every product with what is at this place (0 where nothing is), this place's
    reorder level where set, and whether it ever stocked it. Switched-off products
    are left out unless they still hold units here (or `include_inactive`)."""
    k, c = _place_q(kind, code)
    sql = ("SELECT p.barcode, p.name, p.price, COALESCE(r.level, p.critical_stock_level) AS critical_stock_level, "
           "p.is_active, COALESCE(l.quantity, 0) AS quantity, (l.product_barcode IS NOT NULL) AS stocked_here "
           "FROM products p LEFT JOIN stock_levels l ON l.product_barcode = p.barcode "
           "AND l.location_kind = %s AND l.location_code = %s "
           "LEFT JOIN location_reorder_levels r ON r.product_barcode = p.barcode "
           "AND r.location_kind = %s AND r.location_code = %s "
           + ("" if include_inactive else "WHERE p.is_active OR COALESCE(l.quantity, 0) > 0 ")
           + "ORDER BY p.name, p.barcode")
    return [ProductHere(**{**r, "price": float(r["price"])}) for r in _rows(request, sql, (k, c, k, c))]


@router.get("/stock/units-by-location")
def units_by_location(request: Request, _: Session = Depends(current_session)) -> list[dict]:
    return _rows(request, "SELECT location_kind, location_code, SUM(quantity)::int AS units FROM stock_levels "
                          "GROUP BY location_kind, location_code HAVING SUM(quantity) > 0 "
                          "ORDER BY location_kind, location_code")


@router.get("/stock/in-transit")
def in_transit(request: Request, _: Session = Depends(current_session)) -> dict:
    row = _rows(request, "SELECT COALESCE(SUM(l.expected_qty), 0)::int AS units FROM shipment_lines l "
                         "JOIN shipments s ON s.id = l.shipment_id WHERE s.status = 'in_transit' AND s.stock_moved")
    return {"units": row[0]["units"]}


@router.get("/stock/movement-totals")
def movement_totals(request: Request, since: datetime, _: Session = Depends(current_session)) -> list[dict]:
    """Units in and out per place since `since` (ISO time)."""
    return _rows(request,
                 "SELECT location_kind, location_code, "
                 "SUM(CASE WHEN movement_type = 'receive' THEN quantity ELSE 0 END)::int AS units_in, "
                 "SUM(CASE WHEN movement_type = 'dispatch' THEN quantity ELSE 0 END)::int AS units_out "
                 "FROM stock_movements WHERE location_kind IS NOT NULL AND created_at >= %s "
                 "GROUP BY location_kind, location_code", (since,))


@router.get("/stock/last-moved")
def last_moved(request: Request, kind: Kind, code: str = "", _: Session = Depends(current_session)) -> dict:
    k, c = _place_q(kind, code)
    rows = _rows(request, "SELECT product_barcode, MAX(created_at) AS at FROM stock_movements "
                          "WHERE location_kind = %s AND location_code = %s GROUP BY product_barcode", (k, c))
    return {r["product_barcode"]: r["at"].isoformat() for r in rows}


@router.get("/stock-movements", response_model=list[MovementOut])
def list_movements(request: Request, product_barcode: str | None = None, location_kind: Kind | None = None,
                   location_code: str | None = None, movement_type: Literal["receive", "dispatch"] | None = None,
                   since: datetime | None = None, until: datetime | None = None,
                   limit: int = Query(default=100, ge=1, le=5000),
                   session: Session = Depends(current_session)) -> list[MovementOut]:
    """Newest first. Administrators and depot managers only (it shows who did what)."""
    if session.role not in STOCK_ROLES:
        raise _fail(403, "Only administrators and depot managers may list stock movements.")
    code = location_code.strip().upper() if location_code is not None else None
    rows = _rows(
        request,
        "SELECT m.*, p.name AS product_name FROM stock_movements m JOIN products p ON p.barcode = m.product_barcode "
        "WHERE (%(b)s::text IS NULL OR upper(m.product_barcode) = upper(%(b)s)) "
        "AND (%(k)s::text IS NULL OR m.location_kind = %(k)s) AND (%(c)s::text IS NULL OR m.location_code = %(c)s) "
        "AND (%(t)s::text IS NULL OR m.movement_type = %(t)s) "
        "AND (%(s)s::timestamptz IS NULL OR m.created_at >= %(s)s) "
        "AND (%(u)s::timestamptz IS NULL OR m.created_at < %(u)s) "
        "ORDER BY m.created_at DESC, m.id DESC LIMIT %(n)s",
        {"b": product_barcode, "k": location_kind, "c": code, "t": movement_type, "s": since, "u": until,
         "n": limit})
    return [_movement(r) for r in rows]
