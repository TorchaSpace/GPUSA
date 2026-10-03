"""database.account_repository - accounts, sign-in, lockout, audit, last-admin guard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import database.connection as connection
from database import account_repository as accounts, employee_repository, purchase_order_repository
from database import product_repository, stock_repository, transaction_repository
from database.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    AuthError,
    LastAdminError,
    NotAllowedError,
    SignInFailedError,
)
from shared import auth
from shared.formatting import to_db_timestamp
from shared.models import UNASSIGNED, Employee, LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def people():
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    employee_repository.create(Employee("B-3", "Murat Yılmaz", "Operations", "Warehouse", "WH-01"))
    return accounts.create_first_admin("B-1", "Erol Admin", "482913")


def test_first_admin_creates_the_employee_and_signs_in(people):
    assert (people.badge_id, people.name, people.role, people.area) == ("B-1", "Erol Admin", "admin", auth.AREA_ADMIN)
    assert accounts.admin_exists()
    assert employee_repository.get_by_badge_id("B-1").role == "Management"
    with pytest.raises(AuthError):
        accounts.create_first_admin("B-9", "Someone", "482913")
    events = [e["event"] for e in accounts.list_events()]
    assert events[:2] == ["sign_in", "account_created"]


def test_first_admin_can_be_an_existing_employee():
    employee_repository.create(Employee("B-7", "Ayşe", "Management", "Warehouse", "WH-01", is_active=False))
    session = accounts.create_first_admin("B-7", "", "739184")
    assert session.name == "Ayşe" and employee_repository.get_by_badge_id("B-7").is_active


def test_no_admin_on_a_fresh_system():
    assert not accounts.admin_exists()


def test_cashier_signs_in_at_the_till_only(people):
    accounts.create_account("B-2", "cashier", "5831")
    session = accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS 001")
    assert session.role == "cashier" and accounts.get("B-2").last_sign_in_at
    with pytest.raises(NotAllowedError):
        accounts.authenticate("B-2", "5831", auth.AREA_ADMIN, "Admin")
    with pytest.raises(NotAllowedError):
        accounts.authenticate("B-2", "5831", auth.AREA_DEPOT_CONSOLE, "Depot WH-01")


def test_wrong_badge_and_wrong_pin_look_the_same(people):
    with pytest.raises(SignInFailedError) as unknown:
        accounts.authenticate("B-404", "5831", auth.AREA_POS, "POS")
    with pytest.raises(SignInFailedError) as wrong:
        accounts.authenticate("B-1", "000000", auth.AREA_ADMIN, "Admin")
    assert str(unknown.value).startswith("Badge or PIN is wrong.")
    assert str(wrong.value).startswith("Badge or PIN is wrong.")
    details = {e["detail"] for e in accounts.list_events() if e["event"] == "failed"}
    assert {"unknown badge", "wrong PIN"} <= details


def test_lockout_after_repeated_wrong_pins_and_unlock(people):
    accounts.create_account("B-3", "depot_manager", "7351")
    for attempt in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(SignInFailedError) as info:
            accounts.authenticate("B-3", "9999", auth.AREA_DEPOT_CONSOLE, "Depot")
    assert "1 more try" in str(info.value)
    with pytest.raises(AccountLockedError):
        accounts.authenticate("B-3", "9999", auth.AREA_DEPOT_CONSOLE, "Depot")
    with pytest.raises(AccountLockedError):  # even the right PIN, while locked
        accounts.authenticate("B-3", "7351", auth.AREA_DEPOT_CONSOLE, "Depot")
    assert accounts.is_locked(accounts.get("B-3"))
    accounts.unlock("B-3", by=people.actor)
    assert accounts.authenticate("B-3", "7351", auth.AREA_DEPOT_CONSOLE, "Depot").name == "Murat Yılmaz"
    assert accounts.get("B-3").failed_attempts == 0


def test_lock_expires(people):
    accounts.create_account("B-2", "cashier", "5831")
    past = to_db_timestamp(datetime.now(timezone.utc) - timedelta(minutes=1))
    with connection.connection_scope() as conn:
        conn.execute("UPDATE accounts SET locked_until = ? WHERE id = ?", (past, accounts.get("B-2").id))
    assert accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS").badge_id == "B-2"


def test_switched_off_account_or_employee_is_refused(people):
    accounts.create_account("B-2", "cashier", "5831")
    accounts.set_active("B-2", False, by=people.actor)
    with pytest.raises(AccountDisabledError):
        accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    accounts.set_active("B-2", True)
    selin = employee_repository.get_by_badge_id("B-2")
    selin.is_active = False
    employee_repository.update(selin)
    with pytest.raises(AccountDisabledError):
        accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS")


def test_create_account_validation(people):
    with pytest.raises(ValueError):
        accounts.create_account("B-2", "cashier", "12")
    with pytest.raises(ValueError):
        accounts.create_account("B-2", "admin", "5831")  # admin PIN too short
    with pytest.raises(ValueError):
        accounts.create_account("B-1", "cashier", "5831")  # already has one
    assert accounts.employees_without_account() == [("B-3", "Murat Yılmaz"), ("B-2", "Selin Kaya")]


def test_pin_changes(people):
    accounts.create_account("B-2", "cashier", "5831")
    accounts.set_pin("B-2", "6042", by=people.actor)
    assert accounts.authenticate("B-2", "6042", auth.AREA_POS, "POS")
    with pytest.raises(SignInFailedError):
        accounts.change_own_pin(people, "000000", "739184")
    accounts.change_own_pin(people, "482913", "739184")
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "Admin")


def test_portal_pin_confirmation(people):
    accounts.confirm_pin(people, "482913")
    with pytest.raises(SignInFailedError):
        accounts.confirm_pin(people, "111111")
    assert accounts.list_events()[1]["event"] == "pin_confirmed"


def test_the_last_administrator_cannot_be_removed(people):
    with pytest.raises(LastAdminError):
        accounts.set_role("B-1", "cashier")
    with pytest.raises(LastAdminError):
        accounts.set_active("B-1", False)
    with pytest.raises(LastAdminError):
        accounts.delete("B-1")
    me = employee_repository.get_by_badge_id("B-1")
    me.is_active = False
    with pytest.raises(LastAdminError):
        employee_repository.update(me)
    with pytest.raises(LastAdminError):
        employee_repository.delete("B-1")
    accounts.create_account("B-3", "admin", "739184")
    accounts.set_role("B-1", "depot_manager", by=people.actor)  # fine now: B-3 is an admin
    assert accounts.get("B-1").role == "depot_manager"


def test_sign_out_is_logged(people):
    accounts.sign_out(people)
    assert accounts.list_events(1)[0]["event"] == "sign_out"
    assert accounts.list_events(1)[0]["name"] == "Erol Admin"


def test_who_did_it_is_recorded(people):
    product_repository.create(Product("BOX", "Carton", 40, 10, 1))
    stock_repository.set_count(UNASSIGNED, "BOX", 9, actor=people.actor)
    assert stock_repository.list_movements(1)[0]["handled_by"] == "Erol Admin · B-1"
    sale = transaction_repository.finalize_transaction(Transaction(items=[LineItem("BOX", "Carton", 40, 1)]),
                                                       cashier=people.actor)
    assert transaction_repository.get_by_id(sale.id).cashier == "Erol Admin · B-1"
    order = purchase_order_repository.submit("BOX", "Acme", 5, 10.0, "WH-01 · Test", raised_by=people.actor)
    assert order.raised_by == "Erol Admin · B-1" and order.status == "pending"
    decided = purchase_order_repository.approve(order.id, decided_by=people.actor)
    assert decided.decided_by == "Erol Admin · B-1"
