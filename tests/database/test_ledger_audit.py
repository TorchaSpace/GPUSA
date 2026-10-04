"""The ledger's audit trail: every change is recorded, with who and what,
in the same transaction as the change - and survives the entry itself."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import database.connection as connection
from database import ledger_repository as ledger
from database.exceptions import DataAccessError, DuplicateLedgerDocumentError, LedgerEntryStateError
from shared.auth import Actor
from shared.models import LedgerEntry
from tests.ledger_support import ACTOR, OTHER


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _entry(**overrides) -> LedgerEntry:
    fields = dict(direction="in", doc_type="check", doc_no="CHK-1", counterparty="Harbor Point",
                  detail="First Coastal Bank", issue_date=date(2026, 9, 2), due_date=date(2026, 9, 26), amount=1000)
    fields.update(overrides)
    return LedgerEntry(**fields)


def _audit_rows():
    with connection.connection_scope() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM ledger_audit ORDER BY id")]


def _count(table):
    with connection.connection_scope() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- every action is audited ----------------------------------------------------


def test_every_action_writes_an_audit_row_with_the_actor():
    created = ledger.create(_entry(), ACTOR)
    edited = _entry(id=created.id, amount=1500, counterparty="Metro Parts")
    ledger.update(edited, OTHER)
    ledger.mark_endorsed(created.id, ACTOR)
    ledger.reopen(created.id, OTHER)
    ledger.mark_cleared(created.id, ACTOR)
    ledger.reopen(created.id, ACTOR)
    ledger.delete(created.id, OTHER)

    records = ledger.list_audit(created.id)
    assert [r.action for r in records] == ["created", "edited", "endorsed", "reopened", "cleared", "reopened", "deleted"]
    assert [(r.actor_badge, r.actor_name) for r in records] == [
        ("B-100", "Murat Yılmaz"), ("B-200", "Ayşe Demir"), ("B-100", "Murat Yılmaz"), ("B-200", "Ayşe Demir"),
        ("B-100", "Murat Yılmaz"), ("B-100", "Murat Yılmaz"), ("B-200", "Ayşe Demir"),
    ]
    assert all(r.entry_id == created.id and r.at for r in records)

    first, edit, endorse, reopen, clear, _, deleted = records
    assert first.before is None and first.after["doc_no"] == "CHK-1" and first.after["status"] == "pending"
    assert edit.before["amount"] == 1000 and edit.after["amount"] == 1500
    assert edit.before["counterparty"] == "Harbor Point" and edit.after["counterparty"] == "Metro Parts"
    assert (endorse.before["status"], endorse.after["status"]) == ("pending", "endorsed")
    assert endorse.after["settled_at"] is not None and reopen.after["settled_at"] is None
    assert (clear.before["status"], clear.after["status"]) == ("pending", "cleared")
    assert deleted.before["status"] == "pending" and deleted.after is None


def test_who_columns_follow_the_latest_actor():
    created = ledger.create(_entry(), ACTOR)
    assert created.created_by == "Murat Yılmaz · B-100" == created.updated_by
    assert created.settled_by is None

    cleared = ledger.mark_cleared(created.id, OTHER)
    assert cleared.created_by == "Murat Yılmaz · B-100"
    assert cleared.settled_by == "Ayşe Demir · B-200" == cleared.updated_by

    reopened = ledger.reopen(created.id, ACTOR)
    assert reopened.settled_by is None and reopened.updated_by == "Murat Yılmaz · B-100"

    edited = ledger.update(_entry(id=created.id, amount=5), OTHER)
    assert edited.created_by == "Murat Yılmaz · B-100" and edited.updated_by == "Ayşe Demir · B-200"


def test_recent_audit_is_newest_first_across_entries_and_limited():
    a = ledger.create(_entry(doc_no="A"), ACTOR)
    b = ledger.create(_entry(doc_no="B"), OTHER)
    ledger.mark_cleared(a.id, ACTOR)

    recent = ledger.list_recent_audit(10)
    assert [(r.entry_id, r.action) for r in recent] == [(a.id, "cleared"), (b.id, "created"), (a.id, "created")]
    assert [r.action for r in ledger.list_recent_audit(1)] == ["cleared"]
    assert ledger.list_audit(b.id)[0].actor_name == "Ayşe Demir"
    assert ledger.list_audit(9999) == []


# --- an actor is mandatory ------------------------------------------------------


@pytest.mark.parametrize("actor", [None, Actor("", "Nobody"), Actor("B-1", "  ")])
def test_every_mutator_refuses_without_a_real_actor_and_writes_nothing(actor):
    created = ledger.create(_entry(), ACTOR)
    cleared = ledger.create(_entry(doc_no="CHK-2"), ACTOR)
    ledger.mark_cleared(cleared.id, ACTOR)
    rows_before = _audit_rows()

    calls = (
        lambda: ledger.create(_entry(doc_no="NEW"), actor),
        lambda: ledger.update(_entry(id=created.id, amount=9), actor),
        lambda: ledger.mark_cleared(created.id, actor),
        lambda: ledger.mark_endorsed(created.id, actor),
        lambda: ledger.reopen(cleared.id, actor),
        lambda: ledger.delete(created.id, actor),
    )
    for call in calls:
        with pytest.raises(ValueError) as info:
            call()
        assert "Sign in" in str(info.value)

    assert _audit_rows() == rows_before
    assert ledger.get(created.id).amount == 1000 and ledger.get(created.id).status == "pending"
    assert ledger.get(cleared.id).status == "cleared"
    assert [e.doc_no for e in ledger.list_entries()] == ["CHK-1", "CHK-2"]


def test_omitting_the_actor_is_the_same_refusal():
    with pytest.raises(ValueError):
        ledger.create(_entry())
    with pytest.raises(ValueError):
        ledger.delete(1)
    assert ledger.list_entries() == [] and _audit_rows() == []


# --- refused actions leave no trace --------------------------------------------


def test_refused_actions_write_no_audit_row():
    created = ledger.create(_entry(), ACTOR)
    ledger.mark_cleared(created.id, ACTOR)
    baseline = len(_audit_rows())

    with pytest.raises(DuplicateLedgerDocumentError):
        ledger.create(_entry(), ACTOR)
    with pytest.raises(LedgerEntryStateError):
        ledger.update(_entry(id=created.id, amount=3), ACTOR)  # settled: reopen first
    with pytest.raises(LedgerEntryStateError):
        ledger.mark_cleared(created.id, ACTOR)  # already cleared
    with pytest.raises(LedgerEntryStateError):
        ledger.delete(created.id, ACTOR)  # settled entries can't be deleted
    with pytest.raises(ValueError):
        ledger.create(_entry(doc_no="X", amount=0), ACTOR)
    other = ledger.create(_entry(doc_no="OUT", direction="out"), ACTOR)
    with pytest.raises(ValueError):
        ledger.mark_endorsed(other.id, ACTOR)  # your own outgoing check can't be endorsed

    assert [r["action"] for r in _audit_rows()][baseline:] == ["created"]  # only OUT's creation
    assert ledger.get(created.id).status == "cleared"


# --- delete leaves a trace -----------------------------------------------------


def test_deleting_a_pending_entry_keeps_its_history():
    created = ledger.create(_entry(doc_no="MISTAKE", amount=777), ACTOR)
    ledger.delete(created.id, OTHER)

    assert ledger.list_entries() == []
    records = ledger.list_audit(created.id)  # entry_id is a plain integer: nothing cascades
    assert [r.action for r in records] == ["created", "deleted"]
    assert records[-1].before["doc_no"] == "MISTAKE" and records[-1].before["amount"] == 777
    assert records[-1].actor_label == "Ayşe Demir · B-200"
    assert _count("ledger_audit") == 2


def test_audit_table_has_no_foreign_key_and_refuses_edits_and_deletes():
    created = ledger.create(_entry(), ACTOR)
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA foreign_key_list(ledger_audit)").fetchall() == []
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE ledger_audit SET actor_name = 'someone else'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM ledger_audit")
    assert ledger.list_audit(created.id)[0].actor_name == "Murat Yılmaz"


# --- same transaction: a failure rolls back both -------------------------------


def _break_audit(monkeypatch):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ledger, "_write_audit", boom)


def test_a_failing_audit_write_rolls_back_a_create(monkeypatch):
    _break_audit(monkeypatch)
    with pytest.raises(DataAccessError):
        ledger.create(_entry(), ACTOR)
    assert _count("ledger_entries") == 0 and _count("ledger_audit") == 0


def test_a_failing_audit_write_rolls_back_edit_clear_endorse_reopen_and_delete(monkeypatch):
    created = ledger.create(_entry(), ACTOR)
    settled = ledger.create(_entry(doc_no="CHK-2"), ACTOR)
    ledger.mark_cleared(settled.id, ACTOR)
    snapshot = ([dict(r) for r in _rows("ledger_entries")], _audit_rows())

    _break_audit(monkeypatch)
    for call in (
        lambda: ledger.update(_entry(id=created.id, amount=42), OTHER),
        lambda: ledger.mark_cleared(created.id, OTHER),
        lambda: ledger.mark_endorsed(created.id, OTHER),
        lambda: ledger.reopen(settled.id, OTHER),
        lambda: ledger.delete(created.id, OTHER),
    ):
        with pytest.raises(DataAccessError):
            call()

    assert ([dict(r) for r in _rows("ledger_entries")], _audit_rows()) == snapshot  # nothing moved, nothing logged


def _rows(table):
    with connection.connection_scope() as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()


def test_a_failure_after_the_update_statement_rolls_the_update_back(monkeypatch):
    """The other direction: the UPDATE has run, then something fails - the
    change must not stay, and no audit row appears."""
    created = ledger.create(_entry(), ACTOR)
    real = ledger._row
    calls = {"n": 0}

    def flaky(conn, entry_id):
        calls["n"] += 1
        if calls["n"] == 2:  # the read AFTER the update, i.e. after the change itself
            raise sqlite3.OperationalError("boom")
        return real(conn, entry_id)

    monkeypatch.setattr(ledger, "_row", flaky)
    with pytest.raises(DataAccessError):
        ledger.mark_cleared(created.id, OTHER)
    monkeypatch.setattr(ledger, "_row", real)
    assert ledger.get(created.id).status == "pending"
    assert [r.action for r in ledger.list_audit(created.id)] == ["created"]


# --- "start over" still works with the append-only guards --------------------


def test_erase_all_data_can_wipe_the_audit_table_and_keeps_it_append_only(tmp_path):
    ledger.create(_entry(), ACTOR)
    connection.erase_all_data("ERASE")
    assert _count("ledger_audit") == 0 and _count("ledger_entries") == 0
    created = ledger.create(_entry(), ACTOR)
    with connection.connection_scope() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM ledger_audit")
    assert len(ledger.list_audit(created.id)) == 1
