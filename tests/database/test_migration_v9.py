"""Migration v9: refunds, day closes and the admin audit trail arrive on a v8 database."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_a_v8_database_gains_the_v9_tables(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.execute("PRAGMA user_version = 8")
    old.close()
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION >= 9
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"sale_returns", "sale_return_items", "day_closes", "admin_audit"} <= tables
