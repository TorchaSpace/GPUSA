"""Migration v5: the ledger audit columns and the append-only ledger_audit
table - on old databases, and independent of v4 in either order."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

import database.connection as connection
from database import ledger_repository as ledger, migrations
from shared.models import LedgerEntry
from tests.ledger_support import ACTOR

# ledger_entries exactly as v4 left it: no created_by / settled_by / updated_by, no audit table.
_OLD_LEDGER = """
CREATE TABLE ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    doc_type TEXT NOT NULL CHECK (doc_type IN ('check', 'note', 'transfer', 'invoice')),
    doc_no TEXT NOT NULL, counterparty TEXT NOT NULL, detail TEXT, site TEXT,
    issue_date TEXT NOT NULL, due_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'cleared', 'endorsed')),
    settled_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (due_date >= issue_date),
    UNIQUE (direction, doc_type, doc_no)
);
INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, issue_date, due_date, amount)
    VALUES ('in', 'check', 'OLD-1', 'Legacy Co', '2026-08-01', '2026-08-30', 500),
           ('out', 'invoice', 'OLD-2', 'Legacy Supplier', '2026-08-02', '2026-09-01', 250);
INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, issue_date, due_date, amount, status, settled_at)
    VALUES ('in', 'check', 'OLD-3', 'Legacy Co', '2026-07-01', '2026-07-30', 90, 'cleared', '2026-07-31T10:00:00.000Z');
"""


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _old_db(tmp_path, version: int) -> None:
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_OLD_LEDGER + f"PRAGMA user_version = {version};")
    old.close()


def _ledger_columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(ledger_entries)")}


def test_steps_are_contiguous_and_latest_covers_v5():
    assert migrations.LATEST_VERSION >= 5
    assert sorted(migrations._STEPS) == list(range(1, migrations.LATEST_VERSION + 1))


def test_a_v4_database_gains_the_audit_columns_and_table(tmp_path):
    _old_db(tmp_path, 4)
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert {"created_by", "settled_by", "updated_by"} <= _ledger_columns(conn)
        assert conn.execute("PRAGMA table_info(ledger_audit)").fetchall()
        assert conn.execute("PRAGMA foreign_key_list(ledger_audit)").fetchall() == []
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {"trg_ledger_audit_no_update", "trg_ledger_audit_no_delete"} <= triggers


def test_legacy_rows_survive_with_no_actor_and_can_still_be_worked(tmp_path):
    _old_db(tmp_path, 4)
    entries = ledger.list_entries()
    assert [e.doc_no for e in entries] == ["OLD-3", "OLD-1", "OLD-2"]
    assert all(e.created_by is None and e.settled_by is None and e.updated_by is None for e in entries)
    assert all(ledger.list_audit(e.id) == [] for e in entries)  # nothing recorded before auditing began

    old = next(e for e in entries if e.doc_no == "OLD-1")
    cleared = ledger.mark_cleared(old.id, ACTOR)
    assert cleared.created_by is None and cleared.settled_by == ACTOR.label
    [record] = ledger.list_audit(old.id)  # history starts at the first audited change
    assert record.action == "cleared" and record.before["status"] == "pending" and record.before["doc_no"] == "OLD-1"

    gone = next(e for e in entries if e.doc_no == "OLD-2")
    ledger.delete(gone.id, ACTOR)
    assert ledger.list_audit(gone.id)[0].action == "deleted"
    assert ledger.list_audit(gone.id)[0].before["counterparty"] == "Legacy Supplier"


def test_v5_runs_on_its_own_when_v4_is_already_done_and_with_v4_when_not(tmp_path):
    # v4 done (user_version 4): only the v5 step is applied.
    _old_db(tmp_path, 4)
    seen = []
    original = dict(migrations._STEPS)
    for number, step in original.items():
        migrations._STEPS[number] = (lambda conn, n=number, s=step: (seen.append(n), s(conn))[1])
    try:
        with connection.connection_scope():
            pass
    finally:
        migrations._STEPS.update(original)
    assert seen == list(range(5, migrations.LATEST_VERSION + 1))


def test_a_v3_database_runs_v4_and_v5_in_one_go(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(
        _OLD_LEDGER
        + """CREATE TABLE products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
             stock_quantity INTEGER NOT NULL DEFAULT 0, critical_stock_level INTEGER NOT NULL DEFAULT 0,
             is_active INTEGER NOT NULL DEFAULT 1,
             created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
             updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));
           PRAGMA user_version = 3;"""
    )
    old.close()
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert {"created_by", "settled_by", "updated_by"} <= _ledger_columns(conn)
        assert "cost_price" in {r[1] for r in conn.execute("PRAGMA table_info(products)")}


def test_migration_is_idempotent_and_the_step_is_safe_to_rerun(tmp_path):
    _old_db(tmp_path, 4)
    with connection.connection_scope() as conn:
        migrations._to_v5(conn)  # columns and table already there: no error, no duplicates
        migrations._to_v5(conn)
        columns = [r[1] for r in conn.execute("PRAGMA table_info(ledger_entries)")]
        assert len(columns) == len(set(columns))
    ledger.create(LedgerEntry("in", "check", "NEW-1", "X", date(2026, 9, 1), date(2026, 9, 2), 5), ACTOR)
    with connection.connection_scope() as conn:  # a second open: nothing to do, nothing lost
        migrations.run_migrations(conn)
        assert conn.execute("SELECT COUNT(*) FROM ledger_audit").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0] == 4


def test_a_new_database_has_everything_from_schema_sql(tmp_path):
    with connection.connection_scope() as conn:
        assert {"created_by", "settled_by", "updated_by"} <= _ledger_columns(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
