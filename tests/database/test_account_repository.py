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
from shared.auth import Actor
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
    # Never says how many tries are left - that would only help a guesser.
    assert str(info.value) == "Badge or PIN is wrong." and "more tr" not in str(info.value)
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
    # Fine now that B-3 is an admin - but B-3 does it, an admin can't demote themselves.
    accounts.set_role("B-1", "depot_manager", by=Actor("B-3", "Murat Yılmaz"))
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


def test_recovery_code_resets_the_pin_and_is_rate_limited(people):
    assert not accounts.has_recovery_code()
    code = accounts.create_recovery_code(people)
    assert accounts.has_recovery_code()
    for _ in range(5):
        with pytest.raises(AuthError):
            accounts.reset_with_recovery_code("AAAA-BBBB-CCCC-DDDD", "B-1", "739184")
    with pytest.raises(AuthError) as locked:  # now locked - even the right code is refused
        accounts.reset_with_recovery_code(code, "B-1", "739184")
    assert "locked" in str(locked.value)


def test_recovery_code_works_when_typed_loosely(people):
    code = accounts.create_recovery_code(people)
    accounts.reset_with_recovery_code(code.lower().replace("-", " "), "B-1", "739184")
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"
    events = [e["event"] for e in accounts.list_events()]
    assert "pin_reset" in events and "recovery_code_created" in events


def test_a_new_recovery_code_cancels_the_old_one(people):
    old = accounts.create_recovery_code(people)
    new = accounts.create_recovery_code(people)
    assert old != new
    with pytest.raises(AuthError):
        accounts.reset_with_recovery_code(old, "B-1", "739184")


def test_security_question_flow_shares_the_lockout_with_the_code(people):
    accounts.set_security_question(people, "482913", "Name of my first pet?", "Pamuk")
    code = accounts.create_recovery_code(people)
    assert accounts.get_security_question() == "Name of my first pet?"
    for _ in range(3):
        with pytest.raises(AuthError):
            accounts.reset_with_security_answer("wrong", "B-1", "739184")
    for _ in range(2):
        with pytest.raises(AuthError):
            accounts.reset_with_recovery_code("AAAA-BBBB-CCCC-DDDD", "B-1", "739184")
    with pytest.raises(AuthError) as locked:  # five wrong tries across both ways: locked
        accounts.reset_with_security_answer("Pamuk", "B-1", "739184")
    assert "locked" in str(locked.value)
    assert code  # unchanged by the lock


def test_security_answer_resets_the_pin_and_ignores_case_and_accents(people):
    accounts.set_security_question(people, "482913", "Hangi şehirde doğdun?", "Şişli")
    accounts.reset_with_security_answer(" SISLI ", "B-1", "739184")
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"


def test_only_an_admin_with_the_right_pin_can_set_the_question(people):
    with pytest.raises(SignInFailedError):
        accounts.set_security_question(people, "000000", "Name of my first pet?", "Pamuk")
    with pytest.raises(ValueError):
        accounts.set_security_question(people, "482913", "Name of my first pet?", "a")
