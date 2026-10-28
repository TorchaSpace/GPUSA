"""Deleting a product, dealership or warehouse leaves an append-only audit row."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import attendance_repository, employee_repository, purchase_order_repository, account_repository, audit_repository, dealership_repository, product_repository, warehouse_repository
from shared.auth import Actor
from database.exceptions import LastAdminError, SelfActionError
from shared.models import Dealership, Employee, PriceRange, Product, Warehouse

ADMIN = Actor("A-1", "Aylin Admin")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    account_repository.create_first_admin("A-1", "Aylin Admin", "482913")  # the actor must be a live administrator


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


def _staff():
    for badge, name in (("C-1", "Can"), ("C-2", "Cem"), ("C-3", "Cana")):
        employee_repository.create(Employee(badge, name, "Sales & service", "Dealership", "HQ"))
    account_repository.create_account("C-1", "cashier", "1357")
    account_repository.create_account("C-2", "cashier", "1357")


def test_employee_account_and_price_band_deletions_are_audited():
    _staff()
    product_repository.create(Product("P", "p", 1, 0, 0))
    purchase_order_repository.set_price_range(PriceRange("P", 1, 2), ADMIN)

    employee_repository.delete("C-1", ADMIN)  # the employee and their sign-in account go
    account_repository.delete("C-2", ADMIN)  # only the account goes
    employee_repository.delete("C-3", ADMIN)  # no account
    purchase_order_repository.delete_price_range("P", ADMIN)

    rows = {(e.kind, e.target): e for e in audit_repository.list_recent() if e.action == "deleted"}
    assert set(rows) == {("employee", "C-1"), ("account", "C-2"), ("employee", "C-3"), ("price_band", "P")}
    assert all(e.actor == "Aylin Admin · A-1" for e in rows.values())
    assert rows[("employee", "C-1")].detail == "Can · sign-in account removed"
    assert rows[("employee", "C-3")].detail == "Cana"
    assert rows[("account", "C-2")].detail == "Cem"
    assert rows[("price_band", "P")].detail == "1-2"
    changed = [e for e in audit_repository.list_recent(kind="price_band") if e.action != "deleted"]
    assert [(e.action, e.detail) for e in changed] == [("created", "1-2")]


def test_a_refused_employee_delete_leaves_no_audit_row_and_keeps_everything():
    _staff()
    employee_repository.create(Employee("W-1", "Worker", "Sales & service", "Dealership", "HQ"))
    attendance_repository.check_in("W-1")
    before = len(audit_repository.list_recent())

    with pytest.raises(LastAdminError):  # the only administrator
        employee_repository.delete("A-1")
    with pytest.raises(SelfActionError):
        employee_repository.delete("A-1", ADMIN)
    with pytest.raises(Exception):  # attendance history keeps the employee row
        employee_repository.delete("W-1", ADMIN)

    assert len(audit_repository.list_recent()) == before
    assert employee_repository.get_by_badge_id("W-1") and employee_repository.get_by_badge_id("A-1")
    assert account_repository.get("A-1")


def test_a_changed_price_band_is_audited_once_and_an_identical_save_is_not():
    product_repository.create(Product("P", "p", 1, 0, 0))
    purchase_order_repository.set_price_range(PriceRange("P", 1, 2), ADMIN)
    purchase_order_repository.set_price_range(PriceRange("P", 1, 2), ADMIN)  # nothing changed
    purchase_order_repository.set_price_range(PriceRange("P", 1, 3), ADMIN)
    assert [(e.action, e.detail) for e in reversed(audit_repository.list_recent(kind="price_band"))] == [
        ("created", "1-2"), ("changed", "1-2 -> 1-3")]
