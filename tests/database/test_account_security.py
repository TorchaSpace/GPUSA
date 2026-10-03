"""Account hardening: promotion needs an admin PIN, no self-demotion, actors
re-checked against the database, session validity, badge normalisation,
identical sign-in failures, security-answer and recovery rules."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import account_repository as accounts, employee_repository
from database.exceptions import (
    AccountLockedError,
    AuthError,
    SelfActionError,
    SessionInvalidError,
    SignInFailedError,
)
from shared import auth
from shared.auth import Actor
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def people():
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    employee_repository.create(Employee("B-3", "Murat Yılmaz", "Operations", "Warehouse", "WH-01"))
    return accounts.create_first_admin("B-1", "Erol Admin", "482913")


# --- 1. promotion to administrator ------------------------------------------------

def test_promoting_to_admin_needs_a_new_admin_pin_in_the_same_call(people):
    accounts.create_account("B-2", "cashier", "1357")  # valid for a cashier, too short for an admin
    with pytest.raises(ValueError):
        accounts.set_role("B-2", "admin", by=people.actor)  # no PIN given
    with pytest.raises(ValueError):
        accounts.set_role("B-2", "admin", by=people.actor, new_pin="1357")  # the old short PIN
    with pytest.raises(ValueError):
        accounts.set_role("B-2", "admin", by=people.actor, new_pin="73918")  # 5 digits: still short
    assert accounts.get("B-2").role == "cashier"
    # nothing was half-applied: the cashier PIN still works as a cashier PIN
    assert accounts.authenticate("B-2", "1357", auth.AREA_POS, "POS").role == "cashier"

    accounts.set_role("B-2", "admin", by=people.actor, new_pin="739184")
    assert accounts.get("B-2").role == "admin"
    assert accounts.authenticate("B-2", "739184", auth.AREA_ADMIN, "Admin").role == "admin"
    with pytest.raises(SignInFailedError):  # the old cashier PIN is gone
        accounts.authenticate("B-2", "1357", auth.AREA_ADMIN, "Admin")


def test_promotion_pin_must_differ_from_the_current_one(people):
    accounts.create_account("B-2", "depot_manager", "739184")  # a 6-digit PIN a manager may have
    with pytest.raises(ValueError):
        accounts.set_role("B-2", "admin", by=people.actor, new_pin="739184")
    accounts.set_role("B-2", "admin", by=people.actor, new_pin="615243")
    assert accounts.get("B-2").role == "admin"


def test_demoting_or_same_role_needs_no_pin(people):
    accounts.create_account("B-2", "admin", "739184")
    accounts.set_role("B-2", "cashier", by=people.actor)
    accounts.set_role("B-2", "cashier", by=people.actor)
    assert accounts.get("B-2").role == "cashier"


# --- 6. sessions and self-service guards -----------------------------------------

def test_an_admin_cannot_demote_switch_off_or_remove_themselves(people):
    accounts.create_account("B-3", "admin", "739184")
    with pytest.raises(SelfActionError):
        accounts.set_role("B-1", "cashier", by=people.actor)
    with pytest.raises(SelfActionError):
        accounts.set_active("B-1", False, by=people.actor)
    with pytest.raises(SelfActionError):
        accounts.delete("B-1", by=people.actor)
    me = employee_repository.get_by_badge_id("B-1")
    me.is_active = False
    with pytest.raises(SelfActionError):
        employee_repository.update(me, by=people.actor)
    with pytest.raises(SelfActionError):
        employee_repository.delete("B-1", by=people.actor)
    assert accounts.get("B-1").role == "admin" and accounts.get("B-1").is_active
    # another administrator may
    accounts.set_active("B-1", False, by=Actor("B-3", "Murat Yılmaz"))
    assert not accounts.get("B-1").is_active


def test_the_actor_is_rechecked_in_the_database(people):
    accounts.create_account("B-3", "admin", "739184")
    accounts.create_account("B-2", "cashier", "5831")
    stale = Actor("B-3", "Murat Yılmaz")
    accounts.set_role("B-3", "cashier", by=people.actor)  # B-3's old session still says admin
    with pytest.raises(SessionInvalidError):
        accounts.set_pin("B-2", "6042", by=stale)
    with pytest.raises(SessionInvalidError):
        accounts.set_active("B-2", False, by=stale)
    with pytest.raises(SessionInvalidError):
        accounts.create_account("B-3", "cashier", "5831", by=stale)
    # but anyone may change their OWN pin
    accounts.set_pin("B-3", "6042", by=stale)
    assert accounts.authenticate("B-3", "6042", auth.AREA_POS, "POS")


def test_a_switched_off_actor_cannot_act(people):
    accounts.create_account("B-3", "admin", "739184")
    accounts.create_account("B-2", "cashier", "5831")
    accounts.set_active("B-3", False, by=people.actor)
    with pytest.raises(SessionInvalidError):
        accounts.unlock("B-2", by=Actor("B-3", "Murat"))


def test_recovery_code_and_question_recheck_the_session(people):
    accounts.create_account("B-3", "admin", "739184")
    other = accounts.authenticate("B-3", "739184", auth.AREA_ADMIN, "Admin")
    accounts.set_role("B-3", "cashier", by=people.actor)  # `other` still claims admin
    with pytest.raises(SessionInvalidError):
        accounts.create_recovery_code(other)
    with pytest.raises(SessionInvalidError):
        accounts.set_security_question(other, "739184", "Name of my first pet?", "Pamuk")
    assert not accounts.has_recovery_code() and accounts.get_security_question() is None


def test_is_session_valid_follows_the_account(people):
    accounts.create_account("B-2", "cashier", "5831")
    session = accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    assert accounts.is_session_valid(session) and accounts.is_session_valid(people)
    assert not accounts.is_session_valid(None)
    accounts.require_valid_session(session)

    accounts.set_active("B-2", False, by=people.actor)
    assert not accounts.is_session_valid(session)
    with pytest.raises(SessionInvalidError):
        accounts.require_valid_session(session)
    accounts.set_active("B-2", True, by=people.actor)
    assert accounts.is_session_valid(session)

    selin = employee_repository.get_by_badge_id("B-2")
    selin.is_active = False
    employee_repository.update(selin)
    assert not accounts.is_session_valid(session)  # employee switched off in Workforce
    selin.is_active = True
    employee_repository.update(selin)

    accounts.set_role("B-2", "depot_manager", by=people.actor)
    assert not accounts.is_session_valid(session)  # role changed since sign-in
    accounts.delete("B-2", by=people.actor)
    assert not accounts.is_session_valid(session)


def test_change_own_pin_uses_the_lockout_and_the_live_account(people):
    accounts.create_account("B-2", "cashier", "5831")
    session = accounts.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(SignInFailedError):
            accounts.change_own_pin(session, "0000", "6042")
    with pytest.raises(AccountLockedError):  # guessing the "current PIN" locks like sign-in does
        accounts.change_own_pin(session, "0000", "6042")
    accounts.unlock("B-2", by=people.actor)
    accounts.set_active("B-2", False, by=people.actor)
    with pytest.raises(SessionInvalidError):
        accounts.change_own_pin(session, "5831", "6042")


# --- 10. sign-in secrecy, recovery -----------------------------------------------

def test_failures_say_the_same_for_unknown_and_real_badges_every_time(people):
    accounts.create_account("B-2", "cashier", "5831")
    messages = set()
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):  # real badge: all attempts before the lock
        with pytest.raises(SignInFailedError) as real:
            accounts.authenticate("B-2", "0000", auth.AREA_POS, "POS")
        messages.add(str(real.value))
    for _ in range(auth.MAX_FAILED_ATTEMPTS + 2):  # unknown badge never locks, never counts down
        with pytest.raises(SignInFailedError) as unknown:
            accounts.authenticate("NOPE-1", "0000", auth.AREA_POS, "POS")
        messages.add(str(unknown.value))
    assert messages == {"Badge or PIN is wrong."}


def test_security_answer_needs_four_characters_after_normalising(people):
    with pytest.raises(ValueError):
        accounts.set_security_question(people, "482913", "Name of my first pet?", "Ab!")
    with pytest.raises(ValueError):
        accounts.set_security_question(people, "482913", "Name of my first pet?", "a b c")  # 3 once normalised
    accounts.set_security_question(people, "482913", "Name of my first pet?", "Pamu!")
    assert accounts.get_security_question() == "Name of my first pet?"
    with pytest.raises(ValueError):
        accounts.validate_security_answer("Name of my first pet?", "abc")
    # a too-short guess is never plausible, even if somehow stored
    with pytest.raises(AuthError):
        accounts.reset_with_security_answer("pam", "B-1", "739184")


def test_recovery_cannot_reset_an_admin_whose_employee_is_inactive(people):
    accounts.create_account("B-3", "admin", "739184")
    accounts.set_security_question(people, "482913", "Name of my first pet?", "Pamuk")
    gone = employee_repository.get_by_badge_id("B-1")
    gone.is_active = False
    employee_repository.update(gone)  # B-3 is still an active admin, so allowed
    with pytest.raises(AuthError) as info:
        accounts.reset_with_security_answer("Pamuk", "B-1", "615243")
    assert "not an active administrator" in str(info.value)
    with pytest.raises(SignInFailedError):  # the PIN was not changed
        accounts.authenticate("B-1", "615243", auth.AREA_ADMIN, "Admin")


def test_recovery_failures_and_successes_share_one_counter_by_design(people):
    # Documented behaviour: the question and the recovery code share their
    # wrong-guess counter (and lock), so trying both doesn't double the guesses.
    accounts.set_security_question(people, "482913", "Name of my first pet?", "Pamuk")
    accounts.create_recovery_code(people)
    for _ in range(3):
        with pytest.raises(AuthError):
            accounts.reset_with_security_answer("wrong", "B-1", "739184")
    for _ in range(2):
        with pytest.raises(AuthError):
            accounts.reset_with_recovery_code("AAAA-BBBB-CCCC-DDDD", "B-1", "739184")
    with pytest.raises(AuthError) as locked:
        accounts.reset_with_recovery_code("AAAA-BBBB-CCCC-DDDD", "B-1", "739184")
    assert "locked" in str(locked.value)


# --- 3. badge normalisation ---------------------------------------------------------

def test_badges_are_matched_in_any_case_with_stray_spaces(people):
    accounts.create_account(" b-2 ", "cashier", "5831")
    assert accounts.get("b-2").badge_id == "B-2"
    session = accounts.authenticate("  b-2", "5831", auth.AREA_POS, "POS")
    assert session.badge_id == "B-2"
    accounts.set_active("b-2", False, by=people.actor)
    assert not accounts.get("B-2").is_active
    assert accounts.list_events(1)[0]["badge_id"] == "B-2"  # logged under the canonical badge


def test_legacy_lowercase_rows_still_sign_in():
    with connection.connection_scope() as conn:  # a row written before badges were upper-cased
        conn.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                     "VALUES ('b-9', 'Old Timer', 'Operations', 'Warehouse', 'WH-01')")
    accounts.create_first_admin("A-1", "Erol", "482913")
    accounts.create_account("B-9", "cashier", "5831")
    assert accounts.authenticate("B-9", "5831", auth.AREA_POS, "POS").badge_id == "b-9"
    assert accounts.authenticate("b-9", "5831", auth.AREA_POS, "POS").badge_id == "b-9"
    assert employee_repository.get_by_badge_id("B-9").badge_id == "b-9"


def test_first_admin_badge_is_normalised_and_validated():
    with pytest.raises(ValueError):
        accounts.create_first_admin("   ", "Erol", "482913")
    with pytest.raises(ValueError):
        accounts.create_first_admin("A-1", "x" * 10_000, "482913")
    session = accounts.create_first_admin(" a-1 ", "Erol", "482913")
    assert session.badge_id == "A-1"
