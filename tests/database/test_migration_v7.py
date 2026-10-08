"""Migration v7: transactions gain payment_method - old sales keep NULL
("not recorded"), new ones store card / cash."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations

# transactions exactly as v6 left it: no payment_method.
_OLD_TRANSACTIONS = """
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    total REAL NOT NULL CHECK (total >= 0),
    dealership_code TEXT,
    cashier TEXT
);
INSERT INTO transactions (total, cashier) VALUES (12.5, 'Ayşe · EMP-1');
"""


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _old_db(tmp_path) -> None:
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_OLD_TRANSACTIONS + "PRAGMA user_version = 6;")
    old.close()


def test_steps_are_contiguous_and_latest_covers_v7():
    assert migrations.LATEST_VERSION >= 7
    assert sorted(migrations._STEPS) == list(range(1, migrations.LATEST_VERSION + 1))


def test_a_v6_database_gains_payment_method_and_old_sales_stay_unrecorded(tmp_path):
    _old_db(tmp_path)
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert conn.execute("SELECT payment_method FROM transactions").fetchone()[0] is None


def test_only_card_or_cash_is_accepted(tmp_path):
    _old_db(tmp_path)
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO transactions (total, payment_method) VALUES (1, 'card')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO transactions (total, payment_method) VALUES (1, 'cheque')")


def test_a_new_database_has_the_column_from_schema_sql():
    with connection.connection_scope() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(transactions)")}
    assert "payment_method" in columns
