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
    LastAdminError,
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

def test_admins_can_be_added_but_only_the_first_one_is_root(people):
    accounts.create_account("B-3", "admin", "739184", by=people.actor)
    assert accounts.get("B-1").is_root and not accounts.get("B-3").is_root
    assert people.is_root
    admin = accounts.authenticate("B-3", "739184", auth.AREA_ADMIN, "Admin")
    assert admin.role == "admin" and not admin.is_root and admin.role_label == "Admin"
    assert people.role_label == "Root"
    with pytest.raises(LastAdminError):  # the root account is never demoted
        accounts.set_role("B-1", "cashier")


def test_root_adds_a_new_person_together_with_the_account(people):
    """A fresh installation has nobody but root: a badge nobody has yet, with a name, adds the person too."""
    from database import warehouse_repository
    from shared.models import Warehouse

    warehouse_repository.create(Warehouse(code="WH-01", name="Main depot"))
    made = accounts.create_account(" d-7 ", "depot_manager", "583920", by=people.actor, name=" Deniz  Ak ",
                                   location_name="Main depot")
    assert (made.badge_id, made.name, made.role, made.location_type) == ("D-7", "Deniz Ak", "depot_manager", "Warehouse")
    assert accounts.authenticate("D-7", "583920", auth.AREA_DEPOT_CONSOLE, "Depot").role == "depot_manager"
    assert accounts.create_account("A-9", "admin", "739184", by=people.actor, name="Ece").location_name == "HQ"
    with pytest.raises(ValueError):  # a cashier belongs to a real dealership
        accounts.create_account("C-1", "cashier", "583920", by=people.actor, name="Can", location_name="Nowhere")
    with pytest.raises(Exception):  # no name: an unknown badge is still refused
        accounts.create_account("C-1", "cashier", "583920", by=people.actor)
    assert "C-1" not in [e.badge_id for e in employee_repository.list_all()]
    # an existing badge ignores the name
    assert accounts.create_account("B-2", "cashier", "135702", by=people.actor, name="Someone Else").name == "Selin Kaya"


def test_iam_user_cannot_manage_accounts(people):
    accounts.create_account("B-2", "cashier", "135702")
    iam = accounts.authenticate("B-2", "135702", auth.AREA_ADMIN, "Admin")
    with pytest.raises(Exception):
        accounts.create_account("B-1", "cashier", "246801", by=iam.actor)
    with pytest.raises(Exception):
        accounts.set_role("B-1", "cashier", by=iam.actor)


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
    # another Admin cannot manage accounts at all - only root does - and root is never switched off
    with pytest.raises(SessionInvalidError):
        accounts.set_active("B-1", False, by=Actor("B-3", "Murat Yılmaz"))
    assert accounts.get("B-1").is_active


def test_the_actor_is_rechecked_in_the_database(people):
    accounts.create_account("B-3", "admin", "739184")
    accounts.create_account("B-2", "cashier", "583102")
    stale = Actor("B-3", "Murat Yılmaz")
    accounts.set_role("B-3", "cashier", by=people.actor)  # B-3's old session still says admin
    with pytest.raises(SessionInvalidError):
        accounts.set_pin("B-2", "604271", by=stale)
    with pytest.raises(SessionInvalidError):
        accounts.set_active("B-2", False, by=stale)
    with pytest.raises(SessionInvalidError):
        accounts.create_account("B-3", "cashier", "583102", by=stale)
    # but anyone may change their OWN pin - with the current one (set_pin refuses a self-reset)
    with pytest.raises(SessionInvalidError):  # a demoted admin cannot "reset" even their own PIN
        accounts.set_pin("B-3", "604271", by=stale)
    own = accounts.authenticate("B-3", "739184", auth.AREA_POS, "POS")
    accounts.change_own_pin(own, "739184", "604271")
    assert accounts.authenticate("B-3", "604271", auth.AREA_POS, "POS")


def test_a_switched_off_actor_cannot_act(people):
    accounts.create_account("B-3", "admin", "739184")
    accounts.create_account("B-2", "cashier", "583102")
    accounts.set_active("B-3", False, by=people.actor)
    with pytest.raises(SessionInvalidError):
        accounts.unlock("B-2", by=Actor("B-3", "Murat"))


def test_recovery_code_and_question_recheck_the_session(people):
    accounts.create_account("B-3", "admin", "739184")
    other = accounts.authenticate("B-3", "739184", auth.AREA_ADMIN, "Admin")
    with pytest.raises(AuthError):  # an Admin who is not root has no say over recovery
        accounts.create_recovery_code(other)
    with pytest.raises(AuthError):
        accounts.set_security_question(other, "739184", "Name of my first pet?", "Pamuk")
    assert not accounts.has_recovery_code() and accounts.get_security_question() is None


def test_is_session_valid_follows_the_account(people):
    accounts.create_account("B-2", "cashier", "583102")
    session = accounts.authenticate("B-2", "583102", auth.AREA_POS, "POS")
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
    accounts.create_account("B-2", "cashier", "583102")
    session = accounts.authenticate("B-2", "583102", auth.AREA_POS, "POS")
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(SignInFailedError):
            accounts.change_own_pin(session, "0000", "604271")
    with pytest.raises(AccountLockedError):  # guessing the "current PIN" locks like sign-in does
        accounts.change_own_pin(session, "0000", "604271")
    accounts.unlock("B-2", by=people.actor)
    accounts.set_active("B-2", False, by=people.actor)
    with pytest.raises(SessionInvalidError):
        accounts.change_own_pin(session, "583102", "604271")


# --- 10. sign-in secrecy, recovery -----------------------------------------------

def test_failures_say_the_same_for_unknown_and_real_badges_every_time(people):
    accounts.create_account("B-2", "cashier", "583102")
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
    gone = employee_repository.get_by_badge_id("B-3")
    gone.is_active = False
    employee_repository.update(gone)  # root B-1 is still active, so allowed
    with pytest.raises(AuthError) as info:
        accounts.reset_with_security_answer("Pamuk", "B-3", "615243")
    assert "not an active administrator" in str(info.value)
    with pytest.raises(SignInFailedError):  # the PIN was not changed
        accounts.authenticate("B-3", "615243", auth.AREA_ADMIN, "Admin")


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
    accounts.create_account(" b-2 ", "cashier", "583102")
    assert accounts.get("b-2").badge_id == "B-2"
    session = accounts.authenticate("  b-2", "583102", auth.AREA_POS, "POS")
    assert session.badge_id == "B-2"
    accounts.set_active("b-2", False, by=people.actor)
    assert not accounts.get("B-2").is_active
    assert accounts.list_events(1)[0]["badge_id"] == "B-2"  # logged under the canonical badge


def test_legacy_lowercase_rows_still_sign_in():
    with connection.connection_scope() as conn:  # a row written before badges were upper-cased
        conn.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                     "VALUES ('b-9', 'Old Timer', 'Operations', 'Warehouse', 'WH-01')")
    accounts.create_first_admin("A-1", "Erol", "482913")
    accounts.create_account("B-9", "cashier", "583102")
    assert accounts.authenticate("B-9", "583102", auth.AREA_POS, "POS").badge_id == "b-9"
    assert accounts.authenticate("b-9", "583102", auth.AREA_POS, "POS").badge_id == "b-9"
    assert employee_repository.get_by_badge_id("B-9").badge_id == "b-9"


def test_first_admin_badge_is_normalised_and_validated():
    with pytest.raises(ValueError):
        accounts.create_first_admin("   ", "Erol", "482913")
    with pytest.raises(ValueError):
        accounts.create_first_admin("A-1", "x" * 10_000, "482913")
    session = accounts.create_first_admin(" a-1 ", "Erol", "482913")
    assert session.badge_id == "A-1"


def test_signing_in_upgrades_a_hash_made_with_fewer_iterations(monkeypatch):
    from shared import auth as _auth
    from database import employee_repository
    from shared.models import Employee

    employee_repository.create(Employee("B-7", "Old Hash", "Sales & service", "Dealership", "Harbor Point"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-7", "cashier", "583102")
    with connection.connection_scope() as conn:
        conn.execute("UPDATE accounts SET pin_hash = ? WHERE employee_id = (SELECT id FROM employees WHERE badge_id = 'B-7')",
                     (_auth.hash_pin("583102", iterations=100),))
        conn.commit()
    accounts.authenticate("B-7", "583102", "pos", "Till 1")
    with connection.connection_scope() as conn:
        stored = conn.execute("SELECT pin_hash FROM accounts a JOIN employees e ON e.id = a.employee_id "
                              "WHERE e.badge_id = 'B-7'").fetchone()[0]
    assert stored.split("$")[1] == str(_auth.PBKDF2_ITERATIONS)
    accounts.authenticate("B-7", "583102", "pos", "Till 1")  # still works after the upgrade


# --- the wrong-PIN count is cleared by ANY correct PIN check ------------------------

def _failed_attempts(badge: str) -> int:
    return accounts.get(badge).failed_attempts


def test_a_correct_current_pin_clears_earlier_slips_so_they_never_add_up(people):
    session = people
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(SignInFailedError):
            accounts.set_security_question(session, "000000", "First pet?", "Pamuk")
    assert _failed_attempts("B-1") == auth.MAX_FAILED_ATTEMPTS - 1

    accounts.set_security_question(session, "482913", "First pet?", "Pamuk")  # the right PIN
    assert _failed_attempts("B-1") == 0

    # one slip at sign-in later is just a slip, not the fifth strike
    with pytest.raises(SignInFailedError):
        accounts.authenticate("B-1", "000000", auth.AREA_ADMIN, "Admin")
    assert _failed_attempts("B-1") == 1


def test_changing_your_own_pin_resets_the_count(people):
    with pytest.raises(SignInFailedError):
        accounts.change_own_pin(people, "000000", "913574")
    assert _failed_attempts("B-1") == 1
    accounts.change_own_pin(people, "482913", "913574")
    assert _failed_attempts("B-1") == 0


def test_a_reset_of_your_own_pin_needs_the_current_pin_flow(people):
    with pytest.raises(SelfActionError) as caught:
        accounts.set_pin("B-1", "913574", by=people.actor)  # an unlocked screen must not be a takeover
    assert caught.value.action == "reset"
    assert accounts.authenticate("B-1", "482913", auth.AREA_ADMIN, "Admin")  # nothing changed

    with pytest.raises(SignInFailedError):
        accounts.change_own_pin(people, "000000", "913574")
    accounts.change_own_pin(people, "482913", "913574")
    assert accounts.authenticate("B-1", "913574", auth.AREA_ADMIN, "Admin")

    employee_repository.create(Employee("B-4", "Ece", "Management", "Dealership", "HQ"))
    accounts.create_account("B-4", "cashier", "583102")
    accounts.set_pin("B-4", "604271", by=people.actor)  # an administrator may still reset someone else's
    assert accounts.authenticate("B-4", "604271", auth.AREA_POS, "POS")
