"""Migration v8: a v7 database gains stock_requests (from schema.sql) and the version stamp."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_steps_are_contiguous_and_latest_covers_v8():
    assert migrations.LATEST_VERSION >= 8
    assert sorted(migrations._STEPS) == list(range(1, migrations.LATEST_VERSION + 1))


def test_a_v7_database_gains_the_stock_requests_table(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.execute("PRAGMA user_version = 7")
    old.close()
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(stock_requests)")}
    assert {"dealership_code", "product_barcode", "quantity", "status", "shipment_id"} <= columns
