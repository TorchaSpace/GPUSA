"""Treasury & ledger: checks, promissory notes, transfers and invoices, in (receivable) or out (payable).
Same rules as the desktop's ledger_repository.py:

- One row per document; direction + type + number is unique (the number is stored squashed and upper-case).
- Every change - create, edit, clear, endorse, reopen, delete - is written to the append-only ``ledger_audit``
  table in the SAME transaction (a delete keeps its history). Editing and deleting need a PENDING entry.
- Clearing and endorsing only work from pending (so two admins cannot both settle one); only a received check
  or note can be endorsed; reopening only works from cleared or endorsed.
- A site is stored squashed and upper-case and compared by warehouse code.

Who: administrators and depot managers (admin area or depot console) record and read; only administrators
(admin area) edit, delete, clear, endorse and reopen.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import activity, authz, db
from .auth_routes import Session, current_session
from .product_routes import MAX_AMOUNT
from .shipment_routes import DEPOT_AREAS, DEPOT_ROLES
from .stock_routes import _fail, _write

router = APIRouter(tags=["ledger"])

ADMINS = frozenset({"admin", "depot_manager", "cashier"})  # Admin area: root + IAM users
ADMIN_AREA = frozenset({"admin"})
_CENT = Decimal("0.01")
_SNAPSHOT = ("direction", "doc_type", "doc_no", "counterparty", "detail", "site", "issue_date", "due_date", "amount",
             "status", "settled_at")


def _site(text: str | None) -> str | None:
    return " ".join((text or "").split()).upper() or None


def site_code(text: str | None) -> str:
    return (text or "").split("·")[0].strip().upper()


class EntryIn(BaseModel):
    direction: Literal["in", "out"]
    doc_type: Literal["check", "note", "transfer", "invoice"]
    doc_no: str = Field(max_length=500)
    counterparty: str = Field(max_length=1000)
    detail: str | None = Field(default=None, max_length=2000)
    site: str | None = Field(default=None, max_length=500)
    issue_date: date
    due_date: date
    amount: Decimal = Field(gt=0, le=MAX_AMOUNT, allow_inf_nan=False)


class EntryOut(BaseModel):
    id: int
    direction: str
    doc_type: str
    doc_no: str
    counterparty: str
    detail: str | None
    site: str | None
    issue_date: date
    due_date: date
    amount: float
    status: str
    settled_at: datetime | None
    created_at: datetime
    created_by: str | None
    settled_by: str | None
    updated_by: str | None


class AuditOut(BaseModel):
    id: int
    entry_id: int
    at: datetime
    actor_badge: str
    actor_name: str
    action: str
    before: dict | None
    after: dict | None


def _out(row) -> EntryOut:
    return EntryOut(**{k: (float(row[k]) if k == "amount" else row[k]) for k in EntryOut.model_fields})


def _clean(body: EntryIn) -> dict:
    doc_no = " ".join((body.doc_no or "").split()).upper()
    counterparty = (body.counterparty or "").strip()
    if not doc_no:
        raise _fail(422, "A document number is required.")
    if not counterparty:
        raise _fail(422, "A counterparty is required.")
    amount = body.amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    if amount < _CENT:
        raise _fail(422, "The amount must be at least 0.01.")
    if body.due_date < body.issue_date:
        raise _fail(422, "The due date can't be before the issue date.")
    return {"direction": body.direction, "doc_type": body.doc_type, "doc_no": doc_no, "counterparty": counterparty,
            "detail": (body.detail or "").strip() or None, "site": _site(body.site), "issue_date": body.issue_date,
            "due_date": body.due_date, "amount": amount}


def _snapshot(row) -> dict | None:
    if row is None:
        return None
    return {k: (float(row[k]) if k == "amount" else row[k].isoformat() if hasattr(row[k], "isoformat") else row[k])
            for k in _SNAPSHOT}


def _audit_site(row) -> str:
    """The warehouse code an audit row is about (taken from the entry as it was, else as it became)."""
    for snap in (row["after"], row["before"]):
        if isinstance(snap, dict) and snap.get("site"):
            return site_code(snap["site"])
    return ""


def _audit(conn, session: Session, entry_id: int, action: str, before: dict | None, after: dict | None) -> None:
    conn.execute(
        "INSERT INTO ledger_audit (entry_id, actor_badge, actor_name, action, before_json, after_json) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)",
        (entry_id, session.badge_id, session.name, action,
         None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
         None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True)))


def _readers(session: Session) -> None:
    authz.require(session, roles=DEPOT_ROLES, areas=frozenset({"admin", "depot_console"}))


def _entry(conn, entry_id: int, *, lock: bool = False):
    row = conn.execute("SELECT * FROM ledger_entries WHERE id = %s" + (" FOR UPDATE" if lock else ""),
                       (entry_id,)).fetchone()
    if row is None:
        raise _fail(404, f"No ledger entry with id {entry_id}.")
    return row


def _duplicate(doc_no: str) -> Exception:
    return _fail(409, f"Document {doc_no} is already recorded.")


# --- reads ----------------------------------------------------------------------------

@router.get("/ledger", response_model=list[EntryOut])
def list_entries(request: Request, direction: Literal["in", "out"] | None = None, site: str | None = None,
                 session: Session = Depends(current_session)) -> list[EntryOut]:
    """Soonest due first, optionally by direction and/or site (compared by warehouse code)."""
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute("SELECT * FROM ledger_entries WHERE (%s::text IS NULL OR direction = %s) "
                            "ORDER BY due_date, id", (direction, direction)).fetchall()
        own = authz.own_warehouse(conn, session)
    if own is not None:   # a depot manager sees their own warehouse's documents only
        rows = [r for r in rows if site_code(r["site"]) == own.upper()]
    if site is not None:
        wanted = site_code(site)
        rows = [r for r in rows if site_code(r["site"]) == wanted]
    return [_out(r) for r in rows]


@router.get("/ledger/counterparties", response_model=list[str])
def counterparties(request: Request, session: Session = Depends(current_session)) -> list[str]:
    """Names to suggest while typing: everyone in the ledger, every dealership, every supplier seen on a
    purchase order or price band. Sorted, de-duplicated (case-insensitive)."""
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        own = authz.own_warehouse(conn, session)
        if own is None:
            rows = conn.execute(
                "SELECT counterparty AS name FROM ledger_entries UNION SELECT name FROM dealerships "
                "UNION SELECT supplier FROM purchase_orders "
                "UNION SELECT default_supplier FROM purchase_price_ranges WHERE default_supplier IS NOT NULL").fetchall()
        else:   # a depot manager: names from their own warehouse's books, dealerships and price-band suppliers
            rows = conn.execute(
                "SELECT counterparty AS name FROM ledger_entries WHERE split_part(upper(site), '·', 1) = %(w)s "
                "UNION SELECT name FROM dealerships "
                "UNION SELECT supplier FROM purchase_orders WHERE split_part(upper(site), '·', 1) = %(w)s "
                "UNION SELECT default_supplier FROM purchase_price_ranges WHERE default_supplier IS NOT NULL",
                {"w": own.upper()}).fetchall()
    seen: dict[str, str] = {}
    for row in rows:
        name = (row["name"] or "").strip()
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return sorted(seen.values(), key=str.casefold)


@router.get("/ledger/audit", response_model=list[AuditOut])
def recent_audit(request: Request, limit: int = Query(default=50, ge=0, le=1000),
                 session: Session = Depends(current_session)) -> list[AuditOut]:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        own = authz.own_warehouse(conn, session)
        rows = conn.execute("SELECT id, entry_id, at, actor_badge, actor_name, action, before_json AS before, "
                            "after_json AS after FROM ledger_audit ORDER BY id DESC"
                            + ("" if own is not None else " LIMIT %s"), (() if own is not None else (limit,))).fetchall()
        if own is not None:
            rows = [r for r in rows if _audit_site(r) == own.upper()][:limit]
        return [AuditOut(**r) for r in rows]


@router.get("/ledger/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: int, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        row = _entry(conn, entry_id)
        if not authz.same_warehouse(conn, session, row["site"]):
            raise _fail(404, f"No ledger entry with id {entry_id}.")
        return _out(row)


@router.get("/ledger/{entry_id}/audit", response_model=list[AuditOut])
def entry_audit(entry_id: int, request: Request, session: Session = Depends(current_session)) -> list[AuditOut]:
    """The history of one entry, oldest first - also for one that has since been deleted."""
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute("SELECT id, entry_id, at, actor_badge, actor_name, action, before_json AS before, "
                            "after_json AS after FROM ledger_audit WHERE entry_id = %s ORDER BY id",
                            (entry_id,)).fetchall()
        own = authz.own_warehouse(conn, session)
        if own is not None:
            rows = [r for r in rows if _audit_site(r) == own.upper()]
        return [AuditOut(**r) for r in rows]


# --- writes ---------------------------------------------------------------------------

@router.post("/ledger", response_model=EntryOut, status_code=201)
def create_entry(body: EntryIn, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    authz.require(session, roles=DEPOT_ROLES, areas=DEPOT_AREAS)
    f = _clean(body)

    def work(conn):
        authz.scope_warehouse(conn, session, f["site"])   # a depot manager writes their own warehouse's books only
        label = authz.actor_label(session)
        try:
            row = conn.execute(
                "INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, detail, site, issue_date, "
                "due_date, amount, created_by, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING *", (f["direction"], f["doc_type"], f["doc_no"], f["counterparty"], f["detail"], f["site"],
                                f["issue_date"], f["due_date"], f["amount"], label, label)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise _duplicate(f["doc_no"]) from None
        _audit(conn, session, row["id"], "created", None, _snapshot(row))
        code = site_code(row["site"])
        activity.record(conn, "ledger_entry", severity="notice", source="depot" if code else "admin",
                        place=("warehouse", code) if code else None, actor=label, doc_type=row["doc_type"],
                        flow=row["direction"], doc_no=row["doc_no"], counterparty=row["counterparty"],
                        total=float(row["amount"]), due=str(row["due_date"]))
        return _out(row)

    return _write(request, session, DEPOT_ROLES, work)


@router.put("/ledger/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: int, body: EntryIn, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    """Edit a PENDING document's details (settled ones are history: reopen first)."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    f = _clean(body)

    def work(conn):
        old = _entry(conn, entry_id, lock=True)
        if old["status"] != "pending":
            raise _fail(409, f"Entry {entry_id} is {old['status']}; it can't be edited. Reopen it first.")
        try:
            row = conn.execute(
                "UPDATE ledger_entries SET direction = %s, doc_type = %s, doc_no = %s, counterparty = %s, detail = %s, "
                "site = %s, issue_date = %s, due_date = %s, amount = %s, updated_by = %s, updated_at = now() "
                "WHERE id = %s RETURNING *",
                (f["direction"], f["doc_type"], f["doc_no"], f["counterparty"], f["detail"], f["site"],
                 f["issue_date"], f["due_date"], f["amount"], authz.actor_label(session), entry_id)).fetchone()
        except psycopg.errors.UniqueViolation:
            raise _duplicate(f["doc_no"]) from None
        except psycopg.errors.CheckViolation:
            raise _fail(422, "That change breaks a rule of the ledger (for example a due date before the issue date).") from None
        _audit(conn, session, entry_id, "edited", _snapshot(old), _snapshot(row))
        return _out(row)

    return _write(request, session, ADMINS, work)


@router.delete("/ledger/{entry_id}", status_code=204)
def delete_entry(entry_id: int, request: Request, session: Session = Depends(current_session)) -> None:
    """Remove a PENDING entry (a mistake). The entry goes; its history stays in the audit trail."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        old = _entry(conn, entry_id, lock=True)
        if old["status"] != "pending":
            raise _fail(409, f"Entry {entry_id} is {old['status']}; it can't be deleted. Reopen it first.")
        conn.execute("DELETE FROM ledger_entries WHERE id = %s", (entry_id,))
        _audit(conn, session, entry_id, "deleted", _snapshot(old), None)

    _write(request, session, ADMINS, work)


def _transition(entry_id: int, new_status: str, allowed_from: tuple[str, ...], verb: str, audit_action: str,
                request: Request, session: Session) -> EntryOut:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        old = _entry(conn, entry_id, lock=True)
        if old["status"] not in allowed_from:
            raise _fail(409, f"Entry {entry_id} is {old['status']}; it can't be {verb} now.")
        if new_status == "endorsed" and not (old["direction"] == "in" and old["doc_type"] in ("check", "note")):
            raise _fail(422, "Only a received check or note can be endorsed.")
        label = authz.actor_label(session)
        row = conn.execute(
            "UPDATE ledger_entries SET status = %s, settled_at = CASE WHEN %s = 'pending' THEN NULL ELSE now() END, "
            "settled_by = CASE WHEN %s = 'pending' THEN NULL ELSE %s END, updated_by = %s, updated_at = now() "
            "WHERE id = %s RETURNING *", (new_status, new_status, new_status, label, label, entry_id)).fetchone()
        _audit(conn, session, entry_id, audit_action, _snapshot(old), _snapshot(row))
        return _out(row)

    return _write(request, session, ADMINS, work)


@router.post("/ledger/{entry_id}/clear", response_model=EntryOut)
def clear(entry_id: int, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    return _transition(entry_id, "cleared", ("pending",), "cleared", "cleared", request, session)


@router.post("/ledger/{entry_id}/endorse", response_model=EntryOut)
def endorse(entry_id: int, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    return _transition(entry_id, "endorsed", ("pending",), "endorsed", "endorsed", request, session)


@router.post("/ledger/{entry_id}/reopen", response_model=EntryOut)
def reopen(entry_id: int, request: Request, session: Session = Depends(current_session)) -> EntryOut:
    return _transition(entry_id, "pending", ("cleared", "endorsed"), "reopened", "reopened", request, session)
