"""Shipments (depot -> dealership) and dealership stock requests. Same rules as the
desktop's database/shipment_repository.py and stock_request_repository.py:

- Creating a shipment is a plan and moves no stock. Dispatching takes every line off
  the origin warehouse (all or nothing) and puts the goods "on the truck": still in the
  company total, at no place. Cancelling a dispatched shipment puts them back.
- A receipt puts the received units on the dealership's shelf; a shortfall is written
  off as a 'discrepancy'. You cannot receive more than was shipped, or from a shipment
  that never left.
- A dealership asks the depot for a product (one open request per dealership and
  product); the depot plans a shipment from requests or declines them; cancelling a
  shipment re-opens its requests.

Who: administrators and depot managers (depot console / admin) plan, dispatch, re-time,
cancel and decline; tills (cashiers and administrators at a POS) receive and ask.
Everyone signed in may read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import activity, authz, db
from .auth_routes import Session, current_session
from .stock_routes import _change, _clean, _fail, _lock_product, _log, _require_place, _write

router = APIRouter(tags=["shipments"])

DEPOT_ROLES = frozenset({"admin", "depot_manager"})
DEPOT_AREAS = frozenset({"admin", "depot_console"})
TILL_ROLES = frozenset({"admin", "cashier"})
TILL_AREAS = frozenset({"pos"})
ETA_GRACE = timedelta(minutes=5)
MAX_NOTE = 200
MAX_REQUEST_QTY = 100_000


def _number(prefix: str, ident: int) -> str:
    return f"{prefix}-{ident:05d}"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _check_eta(eta: datetime, earliest: datetime, what: str) -> None:
    if _utc(eta) < _utc(earliest) - ETA_GRACE:
        raise _fail(422, f"The arrival time can't be before {what}.")


# --- shipment shapes ----------------------------------------------------------------

class LineOut(BaseModel):
    id: int
    product_barcode: str
    product_name: str
    expected_qty: int
    received_qty: int | None


class ShipmentOut(BaseModel):
    id: int
    origin: str
    origin_code: str | None
    dealership_code: str
    dealership_name: str
    carrier: str
    driver: str | None
    status: str
    departed_at: datetime | None
    planned_eta: datetime
    eta: datetime
    delivered_at: datetime | None
    receipt_note: str | None
    created_at: datetime
    stock_moved: bool
    lines: list[LineOut]


def _shipments(conn, where: str = "", params: tuple | dict = ()) -> list[ShipmentOut]:
    rows = conn.execute("SELECT * FROM shipments " + where + " ORDER BY eta, id", params).fetchall()
    if not rows:
        return []
    lines: dict[int, list[LineOut]] = {r["id"]: [] for r in rows}
    for line in conn.execute("SELECT * FROM shipment_lines WHERE shipment_id = ANY(%s) ORDER BY id",
                             ([r["id"] for r in rows],)).fetchall():
        lines[line["shipment_id"]].append(LineOut(
            id=line["id"], product_barcode=line["product_barcode"], product_name=line["product_name_at_ship"],
            expected_qty=line["expected_qty"], received_qty=line["received_qty"]))
    return [ShipmentOut(**{k: r[k] for k in ShipmentOut.model_fields if k != "lines"}, lines=lines[r["id"]])
            for r in rows]


def _one(conn, shipment_id: int, *, lock: bool = False) -> ShipmentOut:
    if lock:
        if conn.execute("SELECT id FROM shipments WHERE id = %s FOR UPDATE", (shipment_id,)).fetchone() is None:
            raise _fail(404, f"No shipment with id {shipment_id}.")
    found = _shipments(conn, "WHERE id = %s", (shipment_id,))
    if not found:
        raise _fail(404, f"No shipment with id {shipment_id}.")
    return found[0]


def _active(shipment: ShipmentOut) -> bool:
    return shipment.status in ("scheduled", "in_transit")


def _read(request: Request, fn):
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return fn(conn)


# --- reads ---------------------------------------------------------------------------

@router.get("/shipments", response_model=list[ShipmentOut])
def list_shipments(request: Request, status_in: str | None = None, dealership_code: str | None = None,
                   origin: str | None = None, origin_code: str | None = None,
                   _: Session = Depends(current_session)) -> list[ShipmentOut]:
    """Soonest ETA first. `status_in` is a comma-separated list of statuses."""
    statuses = [s for s in (status_in or "").split(",") if s]
    return _read(request, lambda conn: _shipments(
        conn,
        "WHERE (%(st)s::text[] IS NULL OR status = ANY(%(st)s)) AND (%(d)s::text IS NULL OR dealership_code = %(d)s) "
        "AND (%(o)s::text IS NULL OR origin = %(o)s) AND (%(oc)s::text IS NULL OR origin_code = %(oc)s)",
        {"st": statuses or None, "d": dealership_code, "o": origin, "oc": origin_code}))


@router.get("/shipments/incoming-count")
def incoming_count(request: Request, dealership_code: str | None = None, _: Session = Depends(current_session)) -> dict:
    row = _read(request, lambda conn: conn.execute(
        "SELECT COUNT(*)::int AS n FROM shipments WHERE status IN ('scheduled', 'in_transit') "
        "AND (%s::text IS NULL OR dealership_code = %s)", (dealership_code, dealership_code)).fetchone())
    return {"count": row["n"]}


@router.get("/shipments/{shipment_id}", response_model=ShipmentOut)
def get_shipment(shipment_id: int, request: Request, _: Session = Depends(current_session)) -> ShipmentOut:
    return _read(request, lambda conn: _one(conn, shipment_id))


# --- plan ----------------------------------------------------------------------------

class LineIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1_000_000)


class ShipmentIn(BaseModel):
    origin: str = Field(min_length=1, max_length=200)
    origin_code: str | None = Field(default=None, max_length=32)
    dealership_code: str = Field(min_length=1, max_length=32)
    carrier: str = Field(min_length=1, max_length=200)
    driver: str | None = Field(default=None, max_length=200)
    eta: datetime
    departure: datetime | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=500)
    request_ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/shipments", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
def create_shipment(body: ShipmentIn, request: Request, session: Session = Depends(current_session)) -> ShipmentOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    origin, carrier = body.origin.strip(), body.carrier.strip()
    if not origin or not carrier:
        raise _fail(422, "Fill in the origin and the carrier.")
    origin_code = (body.origin_code or "").strip().upper() or None
    shop_code = body.dealership_code.strip().upper()
    _check_eta(body.eta, body.departure or datetime.now(timezone.utc),
               "the departure" if body.departure else "now")
    actor = authz.actor_label(session)

    def work(conn):
        if origin_code:
            _require_place(conn, "warehouse", origin_code)
        shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = %s", (shop_code,)).fetchone()
        if shop is None:
            raise _fail(404, f"No dealership with code {shop_code}.")
        if not shop["is_active"]:
            raise _fail(409, f"{shop['name']} is switched off, so it can't be sent shipments.")
        merged: dict[str, tuple[str, int]] = {}
        for line in body.lines:
            product = conn.execute("SELECT barcode, name, is_active FROM products WHERE upper(barcode) = upper(%s)",
                                   (line.barcode.strip(),)).fetchone()
            if product is None:
                raise _fail(404, f"No product with barcode {line.barcode}.")
            if not product["is_active"]:
                raise _fail(409, f"Product {product['barcode']} is switched off.")
            _name, qty = merged.get(product["barcode"], (product["name"], 0))
            merged[product["barcode"]] = (product["name"], qty + line.quantity)
        row = conn.execute(
            "INSERT INTO shipments (origin, origin_code, dealership_code, dealership_name, carrier, driver, "
            "planned_eta, eta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (origin, origin_code, shop_code, shop["name"], carrier, _clean(body.driver), body.eta, body.eta)).fetchone()
        sid = row["id"]
        for barcode, (name, qty) in merged.items():
            conn.execute("INSERT INTO shipment_lines (shipment_id, product_barcode, product_name_at_ship, "
                         "expected_qty) VALUES (%s, %s, %s, %s)", (sid, barcode, name, qty))
        for rid in dict.fromkeys(body.request_ids):
            _plan_request(conn, rid, sid, shop_code, actor)
        activity.record(conn, "shipment_created", source="depot", place=("dealership", shop_code), actor=actor,
                        number=_number("SH", sid), origin=origin, units=sum(q for _, q in merged.values()),
                        lines=len(merged), carrier=carrier)
        return _one(conn, sid)

    return _write(request, session, DEPOT_ROLES, work)


def _plan_request(conn, request_id: int, shipment_id: int, shop_code: str, actor: str) -> None:
    current = conn.execute("SELECT * FROM stock_requests WHERE id = %s FOR UPDATE", (request_id,)).fetchone()
    if current is None:
        raise _fail(404, f"No stock request with id {request_id}.")
    number = _number("RQ", request_id)
    if current["dealership_code"] != shop_code:
        raise _fail(409, f"{number} is from {current['dealership_name']}, not this shipment's dealership.")
    on_line = conn.execute("SELECT 1 FROM shipment_lines WHERE shipment_id = %s AND upper(product_barcode) = upper(%s)",
                           (shipment_id, current["product_barcode"])).fetchone()
    if on_line is None:
        raise _fail(409, f"{number} asks for {current['product_name']}, which is not on this shipment.")
    if current["status"] != "open":
        raise _fail(409, f"{number} can't be planned - it is {current['status']}")
    conn.execute("UPDATE stock_requests SET status = 'planned', shipment_id = %s, decided_by = %s, updated_at = now() "
                 "WHERE id = %s", (shipment_id, actor, request_id))


# --- dispatch / re-time / cancel / receive -------------------------------------------

def _origin(shipment: ShipmentOut) -> tuple[str, str]:
    return ("warehouse", shipment.origin_code) if shipment.origin_code else ("unassigned", "")


@router.post("/shipments/{shipment_id}/dispatch", response_model=ShipmentOut)
def dispatch_shipment(shipment_id: int, request: Request, session: Session = Depends(current_session)) -> ShipmentOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    actor = authz.actor_label(session)

    def work(conn):
        shipment = _one(conn, shipment_id, lock=True)
        number = _number("SH", shipment.id)
        if shipment.status != "scheduled":
            raise _fail(409, f"{number} can't be dispatched - it is {shipment.status.replace('_', ' ')}")
        kind, code = _origin(shipment)
        _require_place(conn, kind, code)
        for line in sorted(shipment.lines, key=lambda l: l.product_barcode):
            _lock_product(conn, line.product_barcode)
            _change(conn, kind, code, line.product_barcode, -line.expected_qty, change_total=False, alert=True,
                    actor=actor)
            _log(conn, kind, code, line.product_barcode, "dispatch", line.expected_qty, reason="shipment",
                 actor=actor, reference=number, note=f"Loaded for {shipment.dealership_name}")
        conn.execute("UPDATE shipments SET status = 'in_transit', departed_at = now(), stock_moved = TRUE, "
                     "updated_at = now() WHERE id = %s", (shipment_id,))
        activity.record(conn, "shipment_dispatched", source="depot", place=("dealership", shipment.dealership_code),
                        actor=actor, number=number, origin=shipment.origin,
                        units=sum(l.expected_qty for l in shipment.lines))
        return _one(conn, shipment_id)

    return _write(request, session, DEPOT_ROLES, work)


class EtaIn(BaseModel):
    eta: datetime


@router.post("/shipments/{shipment_id}/eta", response_model=ShipmentOut)
def retime_shipment(shipment_id: int, body: EtaIn, request: Request,
                    session: Session = Depends(current_session)) -> ShipmentOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)

    def work(conn):
        shipment = _one(conn, shipment_id, lock=True)
        if not _active(shipment):
            raise _fail(409, f"{_number('SH', shipment.id)} can't be re-timed - it is {shipment.status}")
        if shipment.departed_at:
            _check_eta(body.eta, shipment.departed_at, "the departure")
        else:
            _check_eta(body.eta, shipment.created_at, "when the shipment was planned")
        conn.execute("UPDATE shipments SET eta = %s, updated_at = now() WHERE id = %s", (body.eta, shipment_id))
        return _one(conn, shipment_id)

    return _write(request, session, DEPOT_ROLES, work)


@router.post("/shipments/{shipment_id}/cancel", response_model=ShipmentOut)
def cancel_shipment(shipment_id: int, request: Request, session: Session = Depends(current_session)) -> ShipmentOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    actor = authz.actor_label(session)

    def work(conn):
        shipment = _one(conn, shipment_id, lock=True)
        number = _number("SH", shipment.id)
        if not _active(shipment):
            raise _fail(409, f"{number} can't be cancelled - it is {shipment.status}")
        if shipment.stock_moved:
            kind, code = _origin(shipment)
            _require_place(conn, kind, code)
            for line in sorted(shipment.lines, key=lambda l: l.product_barcode):
                _lock_product(conn, line.product_barcode)
                _change(conn, kind, code, line.product_barcode, line.expected_qty, change_total=False)
                _log(conn, kind, code, line.product_barcode, "receive", line.expected_qty, reason="shipment",
                     actor=actor, reference=number, note="Returned - shipment cancelled")
        conn.execute("UPDATE shipments SET status = 'cancelled', stock_moved = FALSE, updated_at = now() "
                     "WHERE id = %s", (shipment_id,))
        _reopen_requests(conn, shipment_id)
        activity.record(conn, "shipment_cancelled", severity="notice", source="depot",
                        place=("dealership", shipment.dealership_code), actor=actor, number=number)
        return _one(conn, shipment_id)

    return _write(request, session, DEPOT_ROLES, work)


def _reopen_requests(conn, shipment_id: int) -> None:
    """The requests a cancelled shipment was meant to fill go back to open (or are closed as
    cancelled when the dealership asked again for the same product meanwhile)."""
    for row in conn.execute("SELECT id, dealership_code, product_barcode FROM stock_requests "
                            "WHERE shipment_id = %s AND status = 'planned' FOR UPDATE", (shipment_id,)).fetchall():
        clash = conn.execute("SELECT 1 FROM stock_requests WHERE dealership_code = %s "
                             "AND upper(product_barcode) = upper(%s) AND status = 'open'",
                             (row["dealership_code"], row["product_barcode"])).fetchone()
        conn.execute("UPDATE stock_requests SET status = %s, shipment_id = NULL, updated_at = now() WHERE id = %s",
                     ("cancelled" if clash else "open", row["id"]))


class ReceiptIn(BaseModel):
    received: dict[str, int] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=500)


@router.post("/shipments/{shipment_id}/receive", response_model=ShipmentOut)
def receive_shipment(shipment_id: int, body: ReceiptIn, request: Request,
                     session: Session = Depends(current_session)) -> ShipmentOut:
    """The dealership checked the shipment in: `received` maps barcode -> units that arrived
    (a line left out counts as received in full)."""
    authz.require(session, roles=TILL_ROLES, areas=TILL_AREAS)
    actor = authz.actor_label(session)
    counts: dict[str, int] = {}
    for barcode, qty in body.received.items():
        if qty < 0:
            raise _fail(422, "Received quantities can't be negative.")
        counts[barcode.strip().upper()] = qty
    note = _clean(body.note)

    def work(conn):
        shipment = _one(conn, shipment_id, lock=True)
        number = _number("SH", shipment.id)
        if not _active(shipment):
            raise _fail(409, f"{number} can't be received - it is {shipment.status}")
        if shipment.status != "in_transit" or not shipment.stock_moved:
            raise _fail(409, f"{number} can't be received - it hasn't been dispatched yet "
                             f"(it is {shipment.status.replace('_', ' ')}). The depot must dispatch it first.")
        expected = {l.product_barcode.upper(): l for l in shipment.lines}
        unknown = set(counts) - set(expected)
        if unknown:
            raise _fail(422, f"{', '.join(sorted(unknown))} is not on {number}.")
        for key, count in counts.items():
            if count > expected[key].expected_qty:
                raise _fail(422, f"{expected[key].product_name}: {count} received but only "
                                 f"{expected[key].expected_qty} were shipped on {number}.")
        shelf = ("dealership", shipment.dealership_code)
        _require_place(conn, *shelf)
        missing = 0
        for line in sorted(shipment.lines, key=lambda l: l.product_barcode):
            barcode = line.product_barcode
            got = counts.get(barcode.upper(), line.expected_qty)
            _lock_product(conn, barcode)
            conn.execute("UPDATE shipment_lines SET received_qty = %s WHERE id = %s", (got, line.id))
            _change(conn, *shelf, barcode, line.expected_qty, change_total=False)
            _log(conn, *shelf, barcode, "receive", line.expected_qty, reason="shipment", actor=actor,
                 reference=number, note=f"From {shipment.origin}")
            short = line.expected_qty - got
            if short:
                _change(conn, *shelf, barcode, -short, change_total=True)
                _log(conn, *shelf, barcode, "dispatch", short, reason="discrepancy", actor=actor, reference=number,
                     note=f"Short {short} of {line.expected_qty} shipped")
                missing += short
        conn.execute("UPDATE shipments SET status = 'delivered', delivered_at = now(), receipt_note = %s, "
                     "departed_at = COALESCE(departed_at, now()), stock_moved = FALSE, updated_at = now() "
                     "WHERE id = %s", (note, shipment_id))
        activity.record(conn, "shipment_short" if missing else "shipment_delivered",
                        severity="warning" if missing else "info", source="pos", place=shelf, actor=actor,
                        number=number, origin=shipment.origin,
                        units=sum(l.expected_qty for l in shipment.lines) - missing, missing=missing,
                        note=note or "")
        return _one(conn, shipment_id)

    return _write(request, session, TILL_ROLES, work)


# --- stock requests -------------------------------------------------------------------

class RequestOut(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    dealership_code: str
    dealership_name: str
    product_barcode: str
    product_name: str
    quantity: int
    note: str | None
    status: str
    requested_by: str | None
    decided_by: str | None
    decision_note: str | None
    shipment_id: int | None
    shipment_status: str | None


_REQUEST_SELECT = ("SELECT r.*, s.status AS shipment_status FROM stock_requests r "
                   "LEFT JOIN shipments s ON s.id = r.shipment_id ")


def _request(conn, request_id: int) -> RequestOut:
    row = conn.execute(_REQUEST_SELECT + "WHERE r.id = %s", (request_id,)).fetchone()
    if row is None:
        raise _fail(404, f"No stock request with id {request_id}.")
    return RequestOut(**{k: row[k] for k in RequestOut.model_fields})


def _clean_request_note(note: str | None) -> str | None:
    text = " ".join((note or "").split()) or None
    if text is not None and len(text) > MAX_NOTE:
        raise _fail(422, f"A note can be at most {MAX_NOTE} characters.")
    return text


@router.get("/stock-requests", response_model=list[RequestOut])
def list_requests(request: Request, status_in: str | None = None, dealership_code: str | None = None,
                  limit: int | None = None, _: Session = Depends(current_session)) -> list[RequestOut]:
    """Newest first."""
    statuses = [s for s in (status_in or "").split(",") if s]

    def read(conn):
        rows = conn.execute(
            _REQUEST_SELECT + "WHERE (%(st)s::text[] IS NULL OR r.status = ANY(%(st)s)) "
            "AND (%(d)s::text IS NULL OR r.dealership_code = %(d)s) ORDER BY r.created_at DESC, r.id DESC "
            "LIMIT %(n)s", {"st": statuses or None, "d": dealership_code, "n": limit}).fetchall()
        return [RequestOut(**{k: r[k] for k in RequestOut.model_fields}) for r in rows]

    return _read(request, read)


@router.get("/stock-requests/open-count")
def open_request_count(request: Request, dealership_code: str | None = None,
                       _: Session = Depends(current_session)) -> dict:
    row = _read(request, lambda conn: conn.execute(
        "SELECT COUNT(*)::int AS n FROM stock_requests WHERE status = 'open' "
        "AND (%s::text IS NULL OR dealership_code = %s)", (dealership_code, dealership_code)).fetchone())
    return {"count": row["n"]}


@router.get("/stock-requests/{request_id}", response_model=RequestOut)
def get_request(request_id: int, request: Request, _: Session = Depends(current_session)) -> RequestOut:
    return _read(request, lambda conn: _request(conn, request_id))


class RequestIn(BaseModel):
    dealership_code: str = Field(min_length=1, max_length=32)
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=MAX_REQUEST_QTY)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/stock-requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
def create_request(body: RequestIn, request: Request, session: Session = Depends(current_session)) -> RequestOut:
    authz.require(session, roles=TILL_ROLES, areas=TILL_AREAS)
    shop_code = body.dealership_code.strip().upper()
    note = _clean_request_note(body.note)
    actor = authz.actor_label(session)

    def work(conn):
        shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = %s FOR SHARE", (shop_code,)).fetchone()
        if shop is None:
            raise _fail(404, f"No dealership with code {shop_code}.")
        if not shop["is_active"]:
            raise _fail(409, f"{shop['name']} is switched off, so its till can't be used.")
        product = _lock_product(conn, body.barcode)
        if not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off.")
        existing = conn.execute("SELECT id FROM stock_requests WHERE dealership_code = %s "
                                "AND upper(product_barcode) = upper(%s) AND status = 'open'",
                                (shop_code, product["barcode"])).fetchone()
        if existing is not None:
            raise _fail(409, f"There is already an open request for {product['name']} "
                             f"({_number('RQ', existing['id'])})")
        row = conn.execute(
            "INSERT INTO stock_requests (dealership_code, dealership_name, product_barcode, product_name, quantity, "
            "note, requested_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (shop_code, shop["name"], product["barcode"], product["name"], body.quantity, note, actor)).fetchone()
        activity.record(conn, "request_new", severity="notice", source="pos", place=("dealership", shop_code),
                        actor=actor, number=_number("RQ", row["id"]), product=product["name"], quantity=body.quantity)
        return _request(conn, row["id"])

    return _write(request, session, TILL_ROLES, work)


def _close_request(request: Request, session: Session, roles, request_id: int, new_status: str, action: str,
                   note: str | None) -> RequestOut:
    note = _clean_request_note(note)
    actor = authz.actor_label(session)

    def work(conn):
        current = conn.execute("SELECT * FROM stock_requests WHERE id = %s FOR UPDATE", (request_id,)).fetchone()
        if current is None:
            raise _fail(404, f"No stock request with id {request_id}.")
        number = _number("RQ", request_id)
        if current["status"] != "open":
            raise _fail(409, f"{number} can't be {action} - it is {current['status']}")
        conn.execute("UPDATE stock_requests SET status = %s, decided_by = %s, decision_note = %s, updated_at = now() "
                     "WHERE id = %s", (new_status, actor, note, request_id))
        if new_status == "declined":
            activity.record(conn, "request_declined", source="depot", place=("dealership", current["dealership_code"]),
                            actor=actor, number=number, product=current["product_name"], reason=note or "")
        return _request(conn, request_id)

    return _write(request, session, roles, work)


@router.post("/stock-requests/{request_id}/cancel", response_model=RequestOut)
def cancel_request(request_id: int, request: Request, session: Session = Depends(current_session)) -> RequestOut:
    """The dealership withdraws an open request."""
    authz.require(session, roles=TILL_ROLES, areas=TILL_AREAS)
    return _close_request(request, session, TILL_ROLES, request_id, "cancelled", "cancelled", None)


class DeclineIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/stock-requests/{request_id}/decline", response_model=RequestOut)
def decline_request(request_id: int, body: DeclineIn, request: Request,
                    session: Session = Depends(current_session)) -> RequestOut:
    """The depot turns an open request down, optionally saying why."""
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    return _close_request(request, session, DEPOT_ROLES, request_id, "declined", "declined", body.note)
