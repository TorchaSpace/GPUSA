"""Dealership stock requests: a till asks its depot for a product, the depot turns it down or plans
a shipment for it. Same rules as the desktop's database/stock_request_repository.py:

- one OPEN request per dealership and product (a second one is 409);
- a request can be withdrawn (cancel) or declined only while it is open: the status change is
  ``UPDATE ... WHERE status = 'open'``, so two terminals acting on one request cannot both win;
- "delivered" is the shipment's own status, joined in on reads, never copied.

Who: cashiers (and administrators) at a till create and withdraw; administrators and depot managers
(depot console) decline. Anyone signed in may read. Planning a shipment from a request is part of
the shipment routes, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, field_validator

from . import authz, db
from .auth_routes import Session, current_session

router = APIRouter(prefix="/stock-requests", tags=["stock requests"])

ASKERS = frozenset({"cashier", "admin"})
ASK_AREAS = frozenset({"pos"})
DECIDERS = frozenset({"admin", "depot_manager"})
DECIDE_AREAS = frozenset({"admin", "depot_console"})
MAX_NOTE_LENGTH = 200
MAX_REQUEST_QUANTITY = 100_000
Status = Literal["open", "planned", "declined", "cancelled"]

_SELECT = ("SELECT r.*, s.status AS shipment_status FROM stock_requests r "
           "LEFT JOIN shipments s ON s.id = r.shipment_id ")


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _clean_note(note: str | None) -> str | None:
    note = " ".join((note or "").split()) or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise _fail(422, f"A note is at most {MAX_NOTE_LENGTH} characters.")
    return note


class StockRequestOut(BaseModel):
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


class StockRequestIn(BaseModel):
    dealership_code: str = Field(min_length=1, max_length=32)
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=MAX_REQUEST_QUANTITY)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("dealership_code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("dealership_code must not be blank")
        return v


class DecisionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _fetch(conn, request_id: int) -> dict:
    row = conn.execute(_SELECT + "WHERE r.id = %s", (request_id,)).fetchone()
    if row is None:
        raise _fail(404, f"No stock request RQ-{request_id:05d}.")
    return row


def _number(request_id: int) -> str:
    return f"RQ-{request_id:05d}"


@router.get("", response_model=list[StockRequestOut])
def list_requests(request: Request, statuses: list[Status] = Query(default=[], alias="status"),
                  dealership_code: str | None = None, limit: int | None = Query(default=None, ge=1, le=5000),
                  _: Session = Depends(current_session)):
    """Newest first. `status` may repeat; none means every status."""
    clauses, params = [], []
    if statuses:
        clauses.append("r.status = ANY(%s)")
        params.append(list(statuses))
    if dealership_code is not None:
        clauses.append("r.dealership_code = %s")
        params.append(dealership_code)
    sql = _SELECT + ("WHERE " + " AND ".join(clauses) + " " if clauses else "") + "ORDER BY r.created_at DESC, r.id DESC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(sql, params).fetchall()


@router.get("/{request_id}", response_model=StockRequestOut)
def get_request(request_id: int, request: Request, _: Session = Depends(current_session)):
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _fetch(conn, request_id)


@router.post("", response_model=StockRequestOut, status_code=status.HTTP_201_CREATED)
def create_request(body: StockRequestIn, request: Request, session: Session = Depends(current_session)):
    """A dealership asks for `quantity` of a product."""
    authz.require(session, roles=ASKERS, areas=ASK_AREAS)
    note = _clean_note(body.note)
    actor = authz.actor_label(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=ASKERS)
        shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = %s",
                            (body.dealership_code,)).fetchone()
        if shop is None:
            raise _fail(404, f"No dealership with code {body.dealership_code}.")
        if not shop["is_active"]:
            raise _fail(409, f"Dealership {shop['name']} is switched off.")
        product = conn.execute("SELECT barcode, name, is_active FROM products WHERE upper(barcode) = %s",
                               (body.barcode.strip().upper(),)).fetchone()
        if product is None:
            raise _fail(404, f"No product with barcode {body.barcode}.")
        if not product["is_active"]:
            raise _fail(409, f"Product {product['barcode']} is switched off.")
        existing = conn.execute(
            "SELECT id FROM stock_requests WHERE dealership_code = %s AND upper(product_barcode) = %s "
            "AND status = 'open'", (body.dealership_code, product["barcode"].upper())).fetchone()
        if existing is not None:
            raise _fail(409, f"{_number(existing['id'])} already asks for {product['name']}; change that one.")
        row = conn.execute(
            "INSERT INTO stock_requests (dealership_code, dealership_name, product_barcode, product_name, "
            "quantity, note, requested_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (body.dealership_code, shop["name"], product["barcode"], product["name"], body.quantity, note,
             actor)).fetchone()
        return _fetch(conn, row["id"])


def _close(request_id: int, new_status: str, action: str, request: Request, session: Session,
           roles: frozenset[str], note: str | None):
    note = _clean_note(note)
    actor = authz.actor_label(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=roles)
        current = _fetch(conn, request_id)
        done = conn.execute(
            "UPDATE stock_requests SET status = %s, decided_by = %s, decision_note = %s, updated_at = now() "
            "WHERE id = %s AND status = 'open'", (new_status, actor, note, request_id)).rowcount
        if done == 0:
            raise _fail(409, f"{_number(request_id)} is {current['status']}; it cannot be {action}.")
        return _fetch(conn, request_id)


@router.post("/{request_id}/cancel", response_model=StockRequestOut)
def cancel_request(request_id: int, request: Request, session: Session = Depends(current_session)):
    """The dealership withdraws an open request."""
    authz.require(session, roles=ASKERS, areas=ASK_AREAS)
    return _close(request_id, "cancelled", "cancelled", request, session, ASKERS, None)


@router.post("/{request_id}/decline", response_model=StockRequestOut)
def decline_request(request_id: int, body: DecisionIn, request: Request,
                    session: Session = Depends(current_session)):
    """The depot turns an open request down, optionally saying why."""
    authz.require(session, roles=DECIDERS, areas=DECIDE_AREAS)
    return _close(request_id, "declined", "declined", request, session, DECIDERS, body.note)
