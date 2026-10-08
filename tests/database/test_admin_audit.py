"""Deleting a product, dealership or warehouse leaves an append-only audit row."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import audit_repository, dealership_repository, product_repository, warehouse_repository
from shared.auth import Actor
from shared.models import Dealership, Product, Warehouse

ADMIN = Actor("A-1", "Aylin Admin")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_deletes_are_recorded_with_who_and_what():
    product_repository.create(Product("X-1", "Spare", 1.0, 0, 1))
    dealership_repository.create(Dealership(code="009", name="Old Shop", region="Metro", city="X"))
    warehouse_repository.create(Warehouse(code="WH-9", name="Annex"))
    product_repository.delete("X-1", ADMIN)
    dealership_repository.delete("009", ADMIN)
    warehouse_repository.delete("WH-9", ADMIN)
    rows = {(e.kind, e.target): e for e in audit_repository.list_recent()}
    assert set(rows) == {("product", "X-1"), ("dealership", "009"), ("warehouse", "WH-9")}
    assert rows[("product", "X-1")].actor == "Aylin Admin · A-1" and rows[("product", "X-1")].detail == "Spare"
    assert rows[("dealership", "009")].detail == "Old Shop"
    assert [e.kind for e in audit_repository.list_recent(kind="warehouse")] == ["warehouse"]


def test_a_refused_delete_leaves_no_trace():
    product_repository.create(Product("X-2", "Stocked", 1.0, 5, 1))
    with pytest.raises(Exception):
        product_repository.delete("X-2", ADMIN)
    assert audit_repository.list_recent() == []


def test_the_trail_cannot_be_rewritten():
    with connection.connection_scope() as conn:
        audit_repository.record(conn, ADMIN, "deleted", "product", "Z")
        conn.commit()
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE admin_audit SET target = 'other'")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM admin_audit")
