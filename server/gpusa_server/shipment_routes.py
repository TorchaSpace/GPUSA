"""Shipments from a warehouse to a dealership: plan, dispatch, re-time, cancel, receive. Same rules as the
desktop's database/shipment_repository.py:

- planning moves no stock; dispatching takes every line off the origin warehouse (unassigned stock when the
  shipment has no origin warehouse) and leaves the goods on the truck (``stock_moved``), still in the company total;
- cancelling a dispatched shipment puts the goods back; the stock requests it was planned from reopen;
- receiving puts the units on the dealership shelf; a SHORTFALL is written off (company total drops, logged
  as a 'discrepancy'); receiving MORE than was shipped is refused;
- every stock step runs in the same transaction as the status change it belongs to, and the shipment row is
  locked first, so two terminals acting on one shipment cannot both move stock.

Who: administrators and depot managers (admin / depot console) plan, dispatch, re-time and cancel; cashiers and
administrators at a till receive. Anyone signed in may read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_UNITS
from .request_routes import _number as request_number
from .stock_routes import _change, _lock_product, _log, _require_place

router = APIRouter(prefix="/shipments", tags=["shipments"])

PLANNERS = frozenset({"admin", "depot_manager"})
PLAN_AREAS = frozenset({"admin", "depot_console"})
RECEIVERS = frozenset({"cashier", "admin"})
RECEIVE_AREAS = frozenset({"pos"})
ETA_GRACE = timedelta(minutes=5)
Status = Literal["scheduled", "in_transit", "delivered", "cancelled"]


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _number(shipment_id: int) -> str:
    return f"SH-{shipment_id:05d}"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.astimezone()


def _check_eta(eta: datetime, earliest: datetime, what: str) -> None:
    if _aware(eta) < _aware(earliest) - ETA_GRACE:
        raise _fail(422, f"The arrival time cannot be before {what}.")


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


class LineIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=MAX_UNITS)


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


class EtaIn(BaseModel):
    eta: datetime


class ReceiptIn(BaseModel):
    received: dict[str, int] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=1000)


def _read(conn, shipment_id: int, *, lock: bool = False) -> dict:
    row = conn.execute("SELECT * FROM shipments WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                       (shipment_id,)).fetchone()
    if row is None:
        raise _fail(404, f"No shipment {_number(shipment_id)}.")
    row["lines"] = conn.execute("SELECT * FROM shipment_lines WHERE shipment_id = %s ORDER BY id",
                                (shipment_id,)).fetchall()
    for line in row["lines"]:
        line["product_name"] = line["product_name_at_ship"]
    return row


def _origin(row: dict) -> tuple[str, str]:
    return ("warehouse", row["origin_code"]) if row["origin_code"] else ("unassigned", "")


def _is_active(row: dict) -> bool:
    return row["status"] in ("scheduled", "in_transit")


def _state_error(row: dict, action: str) -> HTTPException:
    return _fail(409, f"{_number(row['id'])} is {row['status']}; it cannot be {action}.")


def _touch(conn, shipment_id: int, assignments: str, params: tuple = ()) -> None:
    conn.execute(f"UPDATE shipments SET {assignments}, updated_at = now() WHERE id = %s", (*params, shipment_id))


def _run(request: Request, session: Session, roles: frozenset[str], work):
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=roles)
        return work(conn)


@router.get("", response_model=list[ShipmentOut])
def list_shipments(request: Request, statuses: list[Status] = Query(default=[], alias="status"),
                   dealership_code: str | None = None, origin: str | None = None, origin_code: str | None = None,
                   _: Session = Depends(current_session)):
    """Soonest ETA first. `status` may repeat; none means every status."""
    clauses, params = [], []
    if statuses:
        clauses.append("status = ANY(%s)")
        params.append(list(statuses))
    for column, value in (("dealership_code", dealership_code), ("origin", origin), ("origin_code", origin_code)):
        if value is not None:
            clauses.append(f"{column} = %s")
            params.append(value)
    sql = "SELECT * FROM shipments" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY eta, id"
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute(sql, params).fetchall()
        lines = conn.execute("SELECT * FROM shipment_lines WHERE shipment_id = ANY(%s) ORDER BY id",
                             ([r["id"] for r in rows],)).fetchall()
    by_shipment: dict[int, list[dict]] = {r["id"]: [] for r in rows}
    for line in lines:
        line["product_name"] = line["product_name_at_ship"]
        by_shipment[line["shipment_id"]].append(line)
    for r in rows:
        r["lines"] = by_shipment[r["id"]]
    return rows


@router.get("/incoming-count")
def incoming_count(request: Request, dealership_code: str | None = None, _: Session = Depends(current_session)) -> dict:
    """Active (scheduled / in transit) shipments, for one dealership or all."""
    with db.connect(request.app.state.settings) as conn:
        n = conn.execute("SELECT count(*) FROM shipments WHERE status IN ('scheduled', 'in_transit') "
                         "AND (%s::text IS NULL OR dealership_code = %s)", (dealership_code, dealership_code)
                         ).fetchone()[0]
    return {"count": n}


@router.get("/{shipment_id}", response_model=ShipmentOut)
def get_shipment(shipment_id: int, request: Request, _: Session = Depends(current_session)):
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _read(conn, shipment_id)


@router.post("", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
def create_shipment(body: ShipmentIn, request: Request, session: Session = Depends(current_session)):
    """Plan a shipment ("scheduled"). Stock is not checked here; it is taken, and checked, on dispatch."""
    authz.require(session, roles=PLANNERS, areas=PLAN_AREAS)
    origin, carrier = body.origin.strip(), body.carrier.strip()
    driver = (body.driver or "").strip() or None
    origin_code = (body.origin_code or "").strip() or None
    if not origin or not carrier:
        raise _fail(422, "A shipment needs an origin and a carrier.")
    merged: dict[str, int] = {}
    for line in body.lines:
        key = line.barcode.strip().upper()
        merged[key] = merged.get(key, 0) + line.quantity
    _check_eta(body.eta, body.departure if body.departure is not None else datetime.now(timezone.utc),
               "the departure" if body.departure is not None else "now")
    actor = authz.actor_label(session)

    def work(conn):
        if origin_code:
            _require_place(conn, "warehouse", origin_code)
        shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = %s",
                            (body.dealership_code.strip(),)).fetchone()
        if shop is None:
            raise _fail(404, f"No dealership with code {body.dealership_code}.")
        if not shop["is_active"]:
            raise _fail(409, f"Dealership {shop['name']} is switched off.")
        names: dict[str, str] = {}
        canonical: dict[str, int] = {}
        for barcode, qty in merged.items():
            product = conn.execute("SELECT barcode, name, is_active FROM products WHERE upper(barcode) = %s",
                                   (barcode,)).fetchone()
            if product is None:
                raise _fail(404, f"No product with barcode {barcode}.")
            if not product["is_active"]:
                raise _fail(409, f"Product {product['barcode']} is switched off.")
            names[product["barcode"]] = product["name"]
            canonical[product["barcode"]] = canonical.get(product["barcode"], 0) + qty
        shipment_id = conn.execute(
            "INSERT INTO shipments (origin, origin_code, dealership_code, dealership_name, carrier, driver, "
            "planned_eta, eta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (origin, origin_code, body.dealership_code.strip(), shop["name"], carrier, driver, body.eta, body.eta)
        ).fetchone()["id"]
        for barcode, qty in canonical.items():
            conn.execute("INSERT INTO shipment_lines (shipment_id, product_barcode, product_name_at_ship, "
                         "expected_qty) VALUES (%s, %s, %s, %s)", (shipment_id, barcode, names[barcode], qty))
        for request_id in dict.fromkeys(body.request_ids):
            _plan_request(conn, request_id, shipment_id, body.dealership_code.strip(), actor)
        return _read(conn, shipment_id)

    return _run(request, session, PLANNERS, work)


def _plan_request(conn, request_id: int, shipment_id: int, dealership_code: str, actor: str) -> None:
    """Tie an open stock request to the shipment just planned; anything off refuses the whole shipment."""
    current = conn.execute("SELECT * FROM stock_requests WHERE id = %s FOR UPDATE", (request_id,)).fetchone()
    if current is None:
        raise _fail(404, f"No stock request {request_number(request_id)}.")
    number = request_number(request_id)
    if current["dealership_code"] != dealership_code:
        raise _fail(409, f"{number} belongs to {current['dealership_name']}, not to this shipment's dealership.")
    on_line = conn.execute("SELECT 1 FROM shipment_lines WHERE shipment_id = %s AND upper(product_barcode) = %s",
                           (shipment_id, current["product_barcode"].upper())).fetchone()
    if on_line is None:
        raise _fail(409, f"{number} asks for {current['product_name']}, which is not on this shipment.")
    done = conn.execute("UPDATE stock_requests SET status = 'planned', shipment_id = %s, decided_by = %s, "
                        "updated_at = now() WHERE id = %s AND status = 'open'", (shipment_id, actor, request_id)
                        ).rowcount
    if done == 0:
        raise _fail(409, f"{number} is {current['status']}; it cannot be planned.")


@router.post("/{shipment_id}/dispatch", response_model=ShipmentOut)
def dispatch_shipment(shipment_id: int, request: Request, session: Session = Depends(current_session)):
    """The truck left: scheduled -> in_transit, and every line comes off the origin's stock."""
    authz.require(session, roles=PLANNERS, areas=PLAN_AREAS)
    actor = authz.actor_label(session)

    def work(conn):
        row = _read(conn, shipment_id, lock=True)
        if row["status"] != "scheduled":
            raise _state_error(row, "dispatched")
        kind, code = _origin(row)
        if session.role == "depot_manager" and kind != "warehouse":
            raise _fail(403, "Depot managers work at warehouses only.")
        _require_place(conn, kind, code)
        for line in sorted(row["lines"], key=lambda l: l["product_barcode"]):
            _lock_product(conn, line["product_barcode"])
            _change(conn, kind, code, line["product_barcode"], -line["expected_qty"], change_total=False)
            _log(conn, kind, code, line["product_barcode"], "dispatch", line["expected_qty"], reason="shipment",
                 reference=_number(shipment_id), note=f"Loaded for {row['dealership_name']}", actor=actor)
        _touch(conn, shipment_id, "status = 'in_transit', departed_at = now(), stock_moved = TRUE")
        return _read(conn, shipment_id)

    return _run(request, session, PLANNERS, work)


@router.post("/{shipment_id}/eta", response_model=ShipmentOut)
def update_eta(shipment_id: int, body: EtaIn, request: Request, session: Session = Depends(current_session)):
    """A new arrival estimate; the promised one stays in planned_eta so the lateness shows."""
    authz.require(session, roles=PLANNERS, areas=PLAN_AREAS)

    def work(conn):
        row = _read(conn, shipment_id, lock=True)
        if not _is_active(row):
            raise _state_error(row, "re-timed")
        if row["departed_at"]:
            _check_eta(body.eta, row["departed_at"], "the departure")
        else:
            _check_eta(body.eta, row["created_at"], "when the shipment was planned")
        _touch(conn, shipment_id, "eta = %s", (body.eta,))
        return _read(conn, shipment_id)

    return _run(request, session, PLANNERS, work)


@router.post("/{shipment_id}/cancel", response_model=ShipmentOut)
def cancel_shipment(shipment_id: int, request: Request, session: Session = Depends(current_session)):
    """Call it off. A dispatched shipment's goods go back to the origin; its stock requests reopen."""
    authz.require(session, roles=PLANNERS, areas=PLAN_AREAS)
    actor = authz.actor_label(session)

    def work(conn):
        row = _read(conn, shipment_id, lock=True)
        if not _is_active(row):
            raise _state_error(row, "cancelled")
        if row["stock_moved"]:
            kind, code = _origin(row)
            if session.role == "depot_manager" and kind != "warehouse":
                raise _fail(403, "Depot managers work at warehouses only.")
            _require_place(conn, kind, code)
            for line in sorted(row["lines"], key=lambda l: l["product_barcode"]):
                _lock_product(conn, line["product_barcode"])
                _change(conn, kind, code, line["product_barcode"], line["expected_qty"], change_total=False)
                _log(conn, kind, code, line["product_barcode"], "receive", line["expected_qty"], reason="shipment",
                     reference=_number(shipment_id), note="Returned - shipment cancelled", actor=actor)
        _touch(conn, shipment_id, "status = 'cancelled', stock_moved = FALSE")
        _reopen_requests(conn, shipment_id)
        return _read(conn, shipment_id)

    return _run(request, session, PLANNERS, work)


def _reopen_requests(conn, shipment_id: int) -> None:
    """Requests the shipment was planned from go back to open, unless the shop asked again meanwhile
    (then the old one is closed as cancelled so there is still just one open request)."""
    for r in conn.execute("SELECT id, dealership_code, product_barcode FROM stock_requests "
                          "WHERE shipment_id = %s AND status = 'planned' ORDER BY id FOR UPDATE",
                          (shipment_id,)).fetchall():
        clash = conn.execute("SELECT 1 FROM stock_requests WHERE dealership_code = %s "
                             "AND upper(product_barcode) = %s AND status = 'open'",
                             (r["dealership_code"], r["product_barcode"].upper())).fetchone()
        conn.execute("UPDATE stock_requests SET status = %s, shipment_id = NULL, updated_at = now() WHERE id = %s",
                     ("cancelled" if clash else "open", r["id"]))


@router.post("/{shipment_id}/receive", response_model=ShipmentOut)
def receive_shipment(shipment_id: int, body: ReceiptIn, request: Request,
                     session: Session = Depends(current_session)):
    """The dealership checked the shipment in. `received` maps barcode -> units actually received; a line left
    out counts as received in full."""
    authz.require(session, roles=RECEIVERS, areas=RECEIVE_AREAS)
    actor = authz.actor_label(session)
    note = " ".join((body.note or "").split()) or None
    counts: dict[str, int] = {}
    for barcode, quantity in body.received.items():
        if quantity < 0:
            raise _fail(422, "Received quantities cannot be negative.")
        counts[barcode.strip().upper()] = quantity

    def work(conn):
        row = _read(conn, shipment_id, lock=True)
        number = _number(shipment_id)
        if not _is_active(row):
            raise _state_error(row, "received")
        if row["status"] != "in_transit" or not row["stock_moved"]:
            raise _fail(409, f"{number} has not been dispatched yet; the depot must send it first.")
        expected = {l["product_barcode"].upper(): l for l in row["lines"]}
        unknown = sorted(set(counts) - set(expected))
        if unknown:
            raise _fail(422, f"{', '.join(unknown)} is not on {number}.")
        for key, count in counts.items():
            if count > expected[key]["expected_qty"]:
                raise _fail(422, f"{count} of {expected[key]['product_name']} received but only "
                                 f"{expected[key]['expected_qty']} were shipped on {number}.")
        _require_place(conn, "dealership", row["dealership_code"])
        for line in sorted(row["lines"], key=lambda l: l["product_barcode"]):
            barcode = line["product_barcode"]
            got = counts.get(barcode.upper(), line["expected_qty"])
            _lock_product(conn, barcode)
            conn.execute("UPDATE shipment_lines SET received_qty = %s WHERE id = %s", (got, line["id"]))
            _change(conn, "dealership", row["dealership_code"], barcode, line["expected_qty"], change_total=False)
            _log(conn, "dealership", row["dealership_code"], barcode, "receive", line["expected_qty"],
                 reason="shipment", reference=number, note=f"From {row['origin']}", actor=actor)
            short = line["expected_qty"] - got
            if short:
                _change(conn, "dealership", row["dealership_code"], barcode, -short, change_total=True)
                _log(conn, "dealership", row["dealership_code"], barcode, "dispatch", short, reason="discrepancy",
                     reference=number, note=f"Short {short} of {line['expected_qty']} shipped", actor=actor)
        _touch(conn, shipment_id, "status = 'delivered', delivered_at = now(), receipt_note = %s, "
                                  "departed_at = COALESCE(departed_at, now()), stock_moved = FALSE", (note,))
        return _read(conn, shipment_id)

    return _run(request, session, RECEIVERS, work)
