"""Server versions of the treasury & ledger repository functions.

ledger_repository calls these instead of SQLite when database.remote is active. Same signatures and return
types as the local functions. Document numbers, the pending-only edit/delete rule, who may settle what and
the audit trail are enforced on the server; its refusals come back as the exceptions the screens catch."""

from __future__ import annotations

import re

from database import remote
from database.exceptions import DuplicateLedgerDocumentError, LedgerEntryNotFoundError, LedgerEntryStateError
from database.remote_stock import db_timestamp
from shared.i18n import UserError
from shared.models import LedgerAuditRecord, LedgerEntry

_ACTIONS = {"edited": "edit", "deleted": "delete", "cleared": "clear", "endorsed": "endorse", "reopened": "reopen"}


def _call(method: str, url: str, body: dict | None = None, *, entry_id: int | None = None):
    try:
        return remote.call(method, url, body)
    except remote.ServerRefusedError as exc:
        text = str(exc)
        if exc.status == 404 and entry_id is not None:
            raise LedgerEntryNotFoundError(entry_id) from None
        if exc.status == 409:
            if m := re.search(r"Document (.+) is already recorded", text):
                raise DuplicateLedgerDocumentError(m.group(1)) from None
            if m := re.search(r"Entry (\d+) is (\w+); it can't be (\w+)", text):
                raise LedgerEntryStateError(int(m.group(1)), m.group(2), _ACTIONS.get(m.group(3), m.group(3))) from None
        if exc.status == 422 and "received check or note" in text:
            raise UserError("err.endorse_only") from None
        raise


def _entry(data: dict) -> LedgerEntry:
    from datetime import date
    return LedgerEntry(
        id=int(data["id"]), direction=data["direction"], doc_type=data["doc_type"], doc_no=data["doc_no"],
        counterparty=data["counterparty"], detail=data.get("detail"), site=data.get("site"),
        issue_date=date.fromisoformat(data["issue_date"]), due_date=date.fromisoformat(data["due_date"]),
        amount=float(data["amount"]), status=data["status"], settled_at=db_timestamp(data.get("settled_at")),
        created_at=db_timestamp(data.get("created_at")), created_by=data.get("created_by"),
        settled_by=data.get("settled_by"), updated_by=data.get("updated_by"))


def _body(entry: LedgerEntry) -> dict:
    return {"direction": entry.direction, "doc_type": entry.doc_type, "doc_no": entry.doc_no,
            "counterparty": entry.counterparty, "detail": entry.detail, "site": entry.site,
            "issue_date": entry.issue_date.isoformat(), "due_date": entry.due_date.isoformat(),
            "amount": entry.amount}


def _audit(data: dict) -> LedgerAuditRecord:
    return LedgerAuditRecord(id=int(data["id"]), entry_id=int(data["entry_id"]), at=db_timestamp(data["at"]),
                             actor_badge=data["actor_badge"], actor_name=data["actor_name"],
                             action=data["action"], before=data.get("before"), after=data.get("after"))


def get(entry_id: int) -> LedgerEntry:
    return _entry(_call("GET", f"/ledger/{int(entry_id)}", entry_id=entry_id))


def list_entries(direction: str | None = None, site: str | None = None) -> list[LedgerEntry]:
    from urllib.parse import urlencode
    query = urlencode({k: v for k, v in (("direction", direction), ("site", site)) if v is not None})
    return [_entry(d) for d in _call("GET", "/ledger" + ("?" + query if query else ""))]


def create(entry: LedgerEntry) -> LedgerEntry:
    return _entry(_call("POST", "/ledger", _body(entry)))


def update(entry: LedgerEntry) -> LedgerEntry:
    return _entry(_call("PUT", f"/ledger/{int(entry.id)}", _body(entry), entry_id=entry.id))


def delete(entry_id: int) -> None:
    _call("DELETE", f"/ledger/{int(entry_id)}", entry_id=entry_id)


def transition(entry_id: int, verb: str) -> LedgerEntry:
    """verb: clear, endorse or reopen."""
    return _entry(_call("POST", f"/ledger/{int(entry_id)}/{verb}", entry_id=entry_id))


def list_audit(entry_id: int) -> list[LedgerAuditRecord]:
    return [_audit(d) for d in _call("GET", f"/ledger/{int(entry_id)}/audit")]


def list_recent_audit(limit: int = 50) -> list[LedgerAuditRecord]:
    return [_audit(d) for d in _call("GET", f"/ledger/audit?limit={max(0, int(limit))}")]


def known_counterparties() -> list[str]:
    return list(_call("GET", "/ledger/counterparties"))
