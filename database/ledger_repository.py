"""All SQL for the Treasury & Ledger domain - the `ledger_entries` table.

One row per financial document (check, promissory note, transfer,
invoice), in (receivable) or out (payable). Recorded in admin_app's
Treasury & Ledger page (company-wide, any site) or depot_app's Manager
Portal > Local Treasury & Ledger (stamped with that depot's site).
Settling - clearing, endorsing, or reopening a mistake - happens in
Admin. Totals, "overdue", due-date wording and the 30-day milestone
strip are computed from these rows by shared/treasury.py, not stored.

Every change - create, edit, clear, endorse, reopen, delete - needs an
`actor` (shared.auth.Actor) and is written to the append-only
`ledger_audit` table in the SAME transaction as the change, so there is
never a change without a trace (deleting a pending entry included: the
entry goes, its history stays). created_by / settled_by / updated_by on the
entry are "name · badge" snapshots of the latest actors. See list_audit()
and list_recent_audit().

Deliberately not in this v1 (see architecture.md): an opening cash
balance / bank-account table (the mockup's "projected balance" line
starts from a made-up $1.84M), bank reconciliation (the mockup's "last
run today, 06:00" footer), partial payments, and automatic links to
purchase orders or sales.
"""

from __future__ import annotations

import functools
import json
import sqlite3
from datetime import date

from shared.i18n import UserError
from database.connection import connection_scope
from database.exceptions import (
    DataAccessError,
    DuplicateLedgerDocumentError,
    LedgerEntryNotFoundError,
    LedgerEntryStateError,
)
from shared.auth import Actor, actor_label
from shared.formatting import round_money
from shared.models import LEDGER_DIRECTIONS, LEDGER_DOC_TYPES, LedgerAuditRecord, LedgerEntry


def _wrap_sqlite(func):
    """A stray sqlite3.Error ("database is locked", a CHECK failure we
    didn't anticipate) becomes a DataAccessError, so callers never see
    SQLite exceptions. ValueError and our own errors pass through."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except sqlite3.Error as exc:
            raise DataAccessError(f"Database error: {exc}") from exc

    return wrapper


def normalise_site(site: str | None) -> str | None:
    """Sites are stored stripped and upper-cased, so "wh-01" and "WH-01"
    are one site. Blank -> None (a company-level document)."""
    return " ".join((site or "").split()).upper() or None


def normalise_doc_no(doc_no: str | None) -> str:
    """Whitespace collapsed, upper-cased - so "chk-1", "CHK-1" and
    "chk  1" collide on the UNIQUE (direction, doc_type, doc_no) key."""
    return " ".join((doc_no or "").split()).upper()


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
        created_by=row["created_by"],
        settled_by=row["settled_by"],
        updated_by=row["updated_by"],
    )


def _clean(entry: LedgerEntry) -> LedgerEntry:
    """Validate and normalise before writing - clearer errors than a raw
    CHECK-constraint failure. Raises ValueError."""
    if entry.direction not in LEDGER_DIRECTIONS:
        raise ValueError(f"direction must be one of {LEDGER_DIRECTIONS!r}")
    if entry.doc_type not in LEDGER_DOC_TYPES:
        raise ValueError(f"document type must be one of {LEDGER_DOC_TYPES!r}")
    doc_no = normalise_doc_no(entry.doc_no)
    counterparty = (entry.counterparty or "").strip()
    if not doc_no:
        raise UserError("err.doc_no_required")
    if not counterparty:
        raise UserError("err.counterparty_required")
    amount = round_money(entry.amount, "Amount")  # finite, >= 0.01 after rounding, <= MAX_AMOUNT
    if entry.due_date < entry.issue_date:
        raise UserError("err.due_before_issue")
    entry.doc_no = doc_no
    entry.counterparty = counterparty
    entry.detail = (entry.detail or "").strip() or None
    entry.site = normalise_site(entry.site)
    entry.amount = amount
    return entry


@_wrap_sqlite
def get(entry_id: int) -> LedgerEntry:
    with connection_scope() as conn:
        row = conn.execute("SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise LedgerEntryNotFoundError(entry_id)
    return _row_to_entry(row)


@_wrap_sqlite
def list_entries(direction: str | None = None, site: str | None = None) -> list[LedgerEntry]:
    """Entries ordered by due date (soonest first), optionally filtered
    by direction and/or site (compared case-insensitively, so rows saved
    before sites were normalised still match)."""
    clauses, params = [], []
    if direction is not None:
        clauses.append("direction = ?")
        params.append(direction)
    sql = "SELECT * FROM ledger_entries"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY due_date, id"
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
    entries = [_row_to_entry(row) for row in rows]
    if site is not None:
        wanted = normalise_site(site)
        entries = [e for e in entries if normalise_site(e.site) == wanted]
    return entries


_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

# The entry fields an audit row snapshots (before_json / after_json).
_SNAPSHOT_FIELDS = (
    "direction", "doc_type", "doc_no", "counterparty", "detail", "site",
    "issue_date", "due_date", "amount", "status", "settled_at",
)


def _require_actor(actor: Actor | None) -> Actor:
    """Every mutator needs to know who is acting. ValueError (UserError,
    so the message follows the app language) when nobody is."""
    if actor is None or not (actor.badge_id or "").strip() or not (actor.name or "").strip():
        raise UserError("err.ledger_actor_required")
    return actor


def _snapshot(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {name: row[name] for name in _SNAPSHOT_FIELDS}


def _write_audit(conn: sqlite3.Connection, entry_id: int, actor: Actor, action: str,
                 before: dict | None, after: dict | None) -> None:
    """Append one audit row. Runs on the CALLER's connection, inside its
    transaction: if this fails the change is rolled back with it."""
    conn.execute(
        "INSERT INTO ledger_audit (entry_id, actor_badge, actor_name, action, before_json, after_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            entry_id, actor.badge_id, actor.name, action,
            None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
            None if after is None else json.dumps(after, ensure_ascii=False, sort_keys=True),
        ),
    )


def _row(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)).fetchone()


def _in_transaction(work):
    """Run work(conn) in one BEGIN IMMEDIATE transaction (all or nothing,
    and two admins on two machines can't interleave)."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = work(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return result


@_wrap_sqlite
def create(entry: LedgerEntry, actor: Actor | None = None) -> LedgerEntry:
    """Record a new (pending) document. Returns it with its id. Raises
    ValueError for invalid fields or a missing `actor`,
    DuplicateLedgerDocumentError if the same direction+type+number is
    already recorded. Audited as 'created'."""
    actor = _require_actor(actor)
    entry = _clean(entry)
    label = actor_label(actor)

    def work(conn):
        try:
            cursor = conn.execute(
                "INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, detail, site, "
                "issue_date, due_date, amount, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    label,
                    label,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateLedgerDocumentError(entry.doc_no) from exc
            raise
        row = _row(conn, cursor.lastrowid)
        _write_audit(conn, row["id"], actor, "created", None, _snapshot(row))
        return row

    return _row_to_entry(_in_transaction(work))


@_wrap_sqlite
def update(entry: LedgerEntry, actor: Actor | None = None) -> LedgerEntry:
    """Edit a PENDING document's details (not its status - see
    mark_*/reopen). Settled entries (cleared/endorsed) are history:
    raises LedgerEntryStateError - reopen it first. Audited as 'edited'
    with the fields before and after."""
    actor = _require_actor(actor)
    if entry.id is None:
        raise UserError("err.ledger_unsaved")
    entry = _clean(entry)

    def work(conn):
        before = _snapshot(_row(conn, entry.id))
        try:
            cursor = conn.execute(
                "UPDATE ledger_entries SET direction = ?, doc_type = ?, doc_no = ?, counterparty = ?, "
                "detail = ?, site = ?, issue_date = ?, due_date = ?, amount = ?, updated_by = ?, "
                f"updated_at = {_NOW} WHERE id = ? AND status = 'pending'",
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
                    actor_label(actor),
                    entry.id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DuplicateLedgerDocumentError(entry.doc_no) from exc
            raise
        if cursor.rowcount == 0:
            _explain_refusal(conn, entry.id, "edit")
        row = _row(conn, entry.id)
        _write_audit(conn, entry.id, actor, "edited", before, _snapshot(row))
        return row

    return _row_to_entry(_in_transaction(work))


def _explain_refusal(conn: sqlite3.Connection, entry_id: int, action: str) -> None:
    """A conditional UPDATE/DELETE matched no row: say why (missing, or
    in the wrong status). Always raises."""
    row = conn.execute("SELECT status FROM ledger_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise LedgerEntryNotFoundError(entry_id)
    raise LedgerEntryStateError(entry_id, row["status"], action)


@_wrap_sqlite
def delete(entry_id: int, actor: Actor | None = None) -> None:
    """Remove a PENDING entry (a mistake). Settled entries are history:
    raises LedgerEntryStateError - reopen first if it really was an error.
    Never silent: the entry's last state is written to the audit trail
    ('deleted', in the same transaction) before the row goes."""
    actor = _require_actor(actor)

    def work(conn):
        before = _snapshot(_row(conn, entry_id))
        cursor = conn.execute("DELETE FROM ledger_entries WHERE id = ? AND status = 'pending'", (entry_id,))
        if cursor.rowcount == 0:
            _explain_refusal(conn, entry_id, "delete")
        _write_audit(conn, entry_id, actor, "deleted", before, None)

    _in_transaction(work)


def _transition(entry_id: int, new_status: str, allowed_from: tuple[str, ...], action: str,
                audit_action: str, actor: Actor | None, extra_where: str = "") -> LedgerEntry:
    """One conditional UPDATE: it only matches while the entry is still in
    a status `allowed_from`, so two admins on two machines can't both
    settle it, and a second clear can't overwrite settled_at. The audit
    row is written in the same transaction."""
    actor = _require_actor(actor)
    label = actor_label(actor)
    settled = _NOW if new_status != "pending" else "NULL"
    marks = ", ".join("?" for _ in allowed_from)

    def work(conn):
        before = _snapshot(_row(conn, entry_id))
        cursor = conn.execute(
            f"UPDATE ledger_entries SET status = ?, settled_at = {settled}, settled_by = ?, "
            f"updated_by = ?, updated_at = {_NOW} "
            f"WHERE id = ? AND status IN ({marks}){extra_where}",
            (new_status, label if new_status != "pending" else None, label, entry_id, *allowed_from),
        )
        if cursor.rowcount == 0:
            row = conn.execute("SELECT status FROM ledger_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise LedgerEntryNotFoundError(entry_id)
            if row["status"] not in allowed_from:
                raise LedgerEntryStateError(entry_id, row["status"], action)
            raise UserError("err.endorse_only")
        row = _row(conn, entry_id)
        _write_audit(conn, entry_id, actor, audit_action, before, _snapshot(row))
        return row

    return _row_to_entry(_in_transaction(work))


@_wrap_sqlite
def mark_cleared(entry_id: int, actor: Actor | None = None) -> LedgerEntry:
    """Paid / collected / cashed. Only from pending (LedgerEntryStateError
    otherwise - e.g. someone else already settled it). Audited as 'cleared'."""
    return _transition(entry_id, "cleared", ("pending",), "clear", "cleared", actor)


@_wrap_sqlite
def mark_endorsed(entry_id: int, actor: Actor | None = None) -> LedgerEntry:
    """A received check or note passed on to someone else (e.g. to pay a
    supplier) instead of being cashed. Only from pending. Raises
    ValueError for anything else - you can't endorse your own outgoing
    check or an invoice. Audited as 'endorsed'."""
    return _transition(
        entry_id, "endorsed", ("pending",), "endorse", "endorsed", actor,
        extra_where=" AND direction = 'in' AND doc_type IN ('check', 'note')",
    )


@_wrap_sqlite
def reopen(entry_id: int, actor: Actor | None = None) -> LedgerEntry:
    """Undo a clear/endorse (e.g. marked by mistake, or a check bounced).
    Only from cleared/endorsed. Audited as 'reopened'."""
    return _transition(entry_id, "pending", ("cleared", "endorsed"), "reopen", "reopened", actor)


def _row_to_audit(row: sqlite3.Row) -> LedgerAuditRecord:
    def load(text):
        try:
            return None if text is None else json.loads(text)
        except ValueError:  # never let one damaged row hide the rest of the history
            return None

    return LedgerAuditRecord(
        id=row["id"], entry_id=row["entry_id"], at=row["at"], actor_badge=row["actor_badge"],
        actor_name=row["actor_name"], action=row["action"],
        before=load(row["before_json"]), after=load(row["after_json"]),
    )


@_wrap_sqlite
def list_audit(entry_id: int) -> list[LedgerAuditRecord]:
    """The history of one entry, oldest first - also for an entry that has
    since been deleted. Empty for a legacy entry nobody has touched since
    auditing began."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM ledger_audit WHERE entry_id = ? ORDER BY id", (entry_id,)).fetchall()
    return [_row_to_audit(row) for row in rows]


@_wrap_sqlite
def list_recent_audit(limit: int = 50) -> list[LedgerAuditRecord]:
    """The latest changes across the whole ledger, newest first."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM ledger_audit ORDER BY id DESC LIMIT ?", (max(0, int(limit)),)).fetchall()
    return [_row_to_audit(row) for row in rows]


@_wrap_sqlite
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
