"""All SQL for the Treasury & Ledger domain - the `ledger_entries` table.

One row per financial document (check, promissory note, transfer,
invoice), in (receivable) or out (payable). Recorded in admin_app's
Treasury & Ledger page (company-wide, any site) or depot_app's Manager
Portal > Local Treasury & Ledger (stamped with that depot's site).
Settling - clearing, endorsing, or reopening a mistake - happens in
Admin. Totals, "overdue", due-date wording and the 30-day milestone
strip are computed from these rows by shared/treasury.py, not stored.

Deliberately not in this v1 (see architecture.md): an opening cash
balance / bank-account table (the mockup's "projected balance" line
starts from a made-up $1.84M), bank reconciliation (the mockup's "last
run today, 06:00" footer), partial payments, and automatic links to
purchase orders or sales.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from database.connection import connection_scope
from database.exceptions import DuplicateLedgerDocumentError, LedgerEntryNotFoundError
from shared.models import LEDGER_DIRECTIONS, LEDGER_DOC_TYPES, LedgerEntry


def _row_to_entry(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        id=row["id"],
        direction=row["direction"],
        doc_type=row["doc_type"],
        doc_no=row["doc_no"],
        counterparty=row["counterparty"],
        detail=row["detail"],
        site=row["site"],
        issue_date=date.fromisoformat(row["issue_date"]),
        due_date=date.fromisoformat(row["due_date"]),
        amount=row["amount"],
        status=row["status"],
        settled_at=row["settled_at"],
        created_at=row["created_at"],
    )


def _clean(entry: LedgerEntry) -> LedgerEntry:
    """Validate and normalise before writing - clearer errors than a raw
    CHECK-constraint failure. Raises ValueError."""
    if entry.direction not in LEDGER_DIRECTIONS:
        raise ValueError(f"direction must be one of {LEDGER_DIRECTIONS!r}")
    if entry.doc_type not in LEDGER_DOC_TYPES:
        raise ValueError(f"document type must be one of {LEDGER_DOC_TYPES!r}")
    doc_no = (entry.doc_no or "").strip()
    counterparty = (entry.counterparty or "").strip()
    if not doc_no:
        raise ValueError("Enter a document number.")
    if not counterparty:
        raise ValueError("Enter a counterparty.")
    if entry.amount is None or entry.amount <= 0:
        raise ValueError("Amount must be greater than 0.")
    if entry.due_date < entry.issue_date:
        raise ValueError("The due date can't be before the issue date.")
    entry.doc_no = doc_no
    entry.counterparty = counterparty
    entry.detail = (entry.detail or "").strip() or None
    entry.site = (entry.site or "").strip() or None
    entry.amount = round(float(entry.amount), 2)
    return entry


def get(entry_id: int) -> LedgerEntry:
    with connection_scope() as conn:
        row = conn.execute("SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise LedgerEntryNotFoundError(entry_id)
    return _row_to_entry(row)


def list_entries(direction: str | None = None, site: str | None = None) -> list[LedgerEntry]:
    """Entries ordered by due date (soonest first), optionally filtered
    by direction and/or site."""
    clauses, params = [], []
    if direction is not None:
        clauses.append("direction = ?")
        params.append(direction)
    if site is not None:
        clauses.append("site = ?")
        params.append(site)
    sql = "SELECT * FROM ledger_entries"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY due_date, id"
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_entry(row) for row in rows]


def create(entry: LedgerEntry) -> LedgerEntry:
    """Record a new (pending) document. Returns it with its id. Raises
    ValueError for invalid fields, DuplicateLedgerDocumentError if the
    same direction+type+number is already recorded."""
    entry = _clean(entry)
    with connection_scope() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, detail, site, "
                "issue_date, due_date, amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.direction,
                    entry.doc_type,
                    entry.doc_no,
                    entry.counterparty,
                    entry.detail,
                    entry.site,
                    entry.issue_date.isoformat(),
                    entry.due_date.isoformat(),
                    entry.amount,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateLedgerDocumentError(entry.doc_no) from exc
            raise
        row = conn.execute("SELECT * FROM ledger_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_entry(row)


def update(entry: LedgerEntry) -> LedgerEntry:
    """Edit a document's details (not its status - see mark_*/reopen).
    Changing an incoming check/note that's been endorsed into a type that
    can't be endorsed is refused."""
    if entry.id is None:
        raise ValueError("Can't update an entry that was never saved.")
    entry = _clean(entry)
    with connection_scope() as conn:
        current = conn.execute("SELECT status FROM ledger_entries WHERE id = ?", (entry.id,)).fetchone()
        if current is None:
            raise LedgerEntryNotFoundError(entry.id)
        if current["status"] == "endorsed" and not _can_endorse(entry.direction, entry.doc_type):
            raise ValueError("Only a received check or note can be endorsed - reopen it first.")
        try:
            conn.execute(
                "UPDATE ledger_entries SET direction = ?, doc_type = ?, doc_no = ?, counterparty = ?, "
                "detail = ?, site = ?, issue_date = ?, due_date = ?, amount = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                (
                    entry.direction,
                    entry.doc_type,
                    entry.doc_no,
                    entry.counterparty,
                    entry.detail,
                    entry.site,
                    entry.issue_date.isoformat(),
                    entry.due_date.isoformat(),
                    entry.amount,
                    entry.id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateLedgerDocumentError(entry.doc_no) from exc
            raise
    return get(entry.id)


def delete(entry_id: int) -> None:
    with connection_scope() as conn:
        cursor = conn.execute("DELETE FROM ledger_entries WHERE id = ?", (entry_id,))
    if cursor.rowcount == 0:
        raise LedgerEntryNotFoundError(entry_id)


def _can_endorse(direction: str, doc_type: str) -> bool:
    return direction == "in" and doc_type in ("check", "note")


def _set_status(entry_id: int, status: str) -> LedgerEntry:
    settled = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" if status != "pending" else "NULL"
    with connection_scope() as conn:
        cursor = conn.execute(
            f"UPDATE ledger_entries SET status = ?, settled_at = {settled}, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (status, entry_id),
        )
    if cursor.rowcount == 0:
        raise LedgerEntryNotFoundError(entry_id)
    return get(entry_id)


def mark_cleared(entry_id: int) -> LedgerEntry:
    """Paid / collected / cashed."""
    return _set_status(entry_id, "cleared")


def mark_endorsed(entry_id: int) -> LedgerEntry:
    """A received check or note passed on to someone else (e.g. to pay a
    supplier) instead of being cashed. Raises ValueError for anything
    else - you can't endorse your own outgoing check or an invoice."""
    entry = get(entry_id)
    if not _can_endorse(entry.direction, entry.doc_type):
        raise ValueError("Only a received check or promissory note can be endorsed.")
    return _set_status(entry_id, "endorsed")


def reopen(entry_id: int) -> LedgerEntry:
    """Undo a clear/endorse (e.g. marked by mistake, or a check bounced)."""
    return _set_status(entry_id, "pending")


def known_counterparties() -> list[str]:
    """Names to suggest while typing a counterparty: everyone already in
    the ledger, every dealership, and every supplier seen on a purchase
    order or safe price band. Sorted, de-duplicated (case-insensitive)."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT counterparty AS name FROM ledger_entries "
            "UNION SELECT name FROM dealerships "
            "UNION SELECT supplier FROM purchase_orders "
            "UNION SELECT default_supplier FROM purchase_price_ranges WHERE default_supplier IS NOT NULL"
        ).fetchall()
    seen: dict[str, str] = {}
    for row in rows:
        name = (row["name"] or "").strip()
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return sorted(seen.values(), key=str.casefold)
