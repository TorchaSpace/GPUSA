"""Employee / dealership validation, badge and code normalisation, location
checks, and what deactivating an employee does to an open shift."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import account_repository as accounts
from database import attendance_repository, dealership_repository, employee_repository
from database.exceptions import (
    AccountNotFoundError,
    DealershipNotFoundError,
    DuplicateBadgeIdError,
    DuplicateDealershipCodeError,
    EmployeeInactiveError,
    EmployeeNotFoundError,
    SelfActionError,
)
from shared.models import Dealership, Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _employee(badge="B-1", **overrides) -> Employee:
    fields = dict(badge_id=badge, name="Elena Varga", title=None, role="Operations",
                  location_type="Warehouse", location_name="Merkez Depo")
    fields.update(overrides)
    return Employee(**fields)


def _dealership(code="CST-04", **overrides) -> Dealership:
    fields = dict(code=code, name="Coastal Parts", region="Coastal", city="Izmir")
    fields.update(overrides)
    return Dealership(**fields)


def _legacy_employee(badge, **fields):
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name, is_active) "
                     "VALUES (?, ?, 'Operations', 'Warehouse', ?, ?)",
                     (badge, fields.get("name", "Legacy"), fields.get("location", "Old Place"), fields.get("active", 1)))


# --- 4. employees: required fields and lengths -------------------------------------

@pytest.mark.parametrize("field,value", [
    ("badge_id", ""), ("badge_id", "   "), ("name", ""), ("name", "   "), ("location_name", ""),
    ("name", "x" * 10_000), ("badge_id", "B" * 500), ("location_name", "w" * 500), ("title", "t" * 500),
])
def test_employee_create_rejects_empty_and_oversized_fields(field, value):
    with pytest.raises(ValueError):
        employee_repository.create(_employee(**{field: value}))
    assert employee_repository.list_all() == []


def test_employee_update_rejects_empty_and_oversized_fields():
    employee_repository.create(_employee())
    for bad in ({"name": ""}, {"name": "x" * 10_000}, {"location_name": " "}):
        with pytest.raises(ValueError):
            employee_repository.update(_employee(**bad))
    assert employee_repository.get_by_badge_id("B-1").name == "Elena Varga"


def test_employee_text_is_trimmed():
    employee_repository.create(_employee(name="  Elena   Varga ", title="  Driver ", location_name=" Merkez  Depo "))
    saved = employee_repository.get_by_badge_id("B-1")
    assert (saved.name, saved.title, saved.location_name) == ("Elena Varga", "Driver", "Merkez Depo")


# --- 3. badge normalisation ----------------------------------------------------------

def test_badges_are_stored_upper_case_and_trimmed():
    employee_repository.create(_employee(" b-7 "))
    assert employee_repository.get_by_badge_id("B-7").badge_id == "B-7"
    assert employee_repository.get_by_badge_id(" b-7").badge_id == "B-7"
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.get_by_badge_id("B-8")


def test_badges_differing_only_in_case_or_spaces_are_duplicates():
    employee_repository.create(_employee("B-7"))
    for twin in (" B-7", "b-7", "B-7 ", " b-7 "):
        with pytest.raises(DuplicateBadgeIdError):
            employee_repository.create(_employee(twin, name="Twin"))
    assert len(employee_repository.list_all()) == 1


def test_a_legacy_lowercase_badge_is_still_found_and_collides_with_its_upper_case_twin():
    _legacy_employee("b-9")
    assert employee_repository.get_by_badge_id("B-9").badge_id == "b-9"
    assert employee_repository.get_by_badge_id("b-9").badge_id == "b-9"
    with pytest.raises(DuplicateBadgeIdError):
        employee_repository.create(_employee("B-9"))
    legacy = employee_repository.get_by_badge_id("B-9")
    legacy.name = "Renamed"
    employee_repository.update(legacy)  # keyed by the stored badge, whatever case it is typed in
    assert employee_repository.get_by_badge_id("b-9").name == "Renamed"
    employee_repository.delete("B-9")
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.get_by_badge_id("b-9")


def test_lowercase_badge_can_clock_in_and_out():
    employee_repository.create(_employee("B-7"))
    attendance_repository.check_in("b-7")
    assert [r["badge_id"] for r in attendance_repository.list_open()] == ["B-7"]
    attendance_repository.check_out(" B-7 ")
    assert attendance_repository.list_open() == []
    _legacy_employee("c-1")  # a lowercase row from before the rule
    attendance_repository.check_in("C-1")
    attendance_repository.check_out("c-1")


# --- 4. location --------------------------------------------------------------------

def _add_warehouse(code="WH-01", name="Merkez Depo"):
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO warehouses (code, name) VALUES (?, ?)", (code, name))


def test_location_check_is_opt_in_so_setup_scripts_keep_working():
    employee_repository.create(_employee(location_name="Nowhere at all"))  # free text allowed by default
    assert employee_repository.get_by_badge_id("B-1").location_name == "Nowhere at all"


def test_location_must_be_an_existing_place_of_the_chosen_type_when_checked():
    _add_warehouse()
    dealership_repository.create(_dealership())
    employee_repository.create(_employee("B-1", location_name="merkez depo"), check_location=True)  # case-insensitive
    employee_repository.create(_employee("B-2", location_name="WH-01"), check_location=True)  # a code works too
    employee_repository.create(
        _employee("B-3", location_type="Dealership", location_name="Coastal Parts"), check_location=True)
    with pytest.raises(ValueError):
        employee_repository.create(_employee("B-4", location_name="Atlantis"), check_location=True)
    with pytest.raises(ValueError):  # right name, wrong type
        employee_repository.create(_employee("B-5", location_name="Coastal Parts"), check_location=True)
    assert sorted(e.badge_id for e in employee_repository.list_all()) == ["B-1", "B-2", "B-3"]


def test_location_is_only_checked_on_update_when_it_changes():
    _legacy_employee("L-1", location="Gone Place")
    legacy = employee_repository.get_by_badge_id("L-1")
    legacy.title = "Lead"
    employee_repository.update(legacy, check_location=True)  # untouched free-text place: still editable
    legacy.location_name = "Still Not A Place"
    with pytest.raises(ValueError):
        employee_repository.update(legacy, check_location=True)
    _add_warehouse()
    legacy.location_name = "Merkez Depo"
    employee_repository.update(legacy, check_location=True)
    assert employee_repository.get_by_badge_id("L-1").location_name == "Merkez Depo"


def test_location_choices_lists_names_for_the_pick_list():
    _add_warehouse("WH-01", "Merkez Depo")
    _add_warehouse("WH-02", "Ege Depo")
    dealership_repository.create(_dealership())
    assert employee_repository.location_choices() == {
        "Warehouse": ["Ege Depo", "Merkez Depo"], "Dealership": ["Coastal Parts"]}
    assert "WH-01" in employee_repository.location_choices(include_codes=True)["Warehouse"]


# --- 4. dealerships ------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("code", ""), ("code", "  "), ("name", ""), ("name", "   "), ("name", "x" * 10_000),
    ("code", "C" * 100), ("city", "c" * 10_000), ("manager_name", "m" * 10_000),
])
def test_dealership_create_rejects_empty_and_oversized_fields(field, value):
    with pytest.raises(ValueError):
        dealership_repository.create(_dealership(**{field: value}))
    assert dealership_repository.list_all() == []


def test_dealership_update_rejects_empty_and_oversized_fields():
    dealership_repository.create(_dealership())
    for bad in ({"name": ""}, {"name": "x" * 10_000}):
        with pytest.raises(ValueError):
            dealership_repository.update(_dealership(**bad))
    assert dealership_repository.get_by_code("CST-04").name == "Coastal Parts"


def test_dealership_codes_are_normalised_and_case_insensitively_unique():
    dealership_repository.create(_dealership(" cst-04 "))
    assert dealership_repository.get_by_code("CST-04").code == "CST-04"
    assert dealership_repository.get_by_code("cst-04 ").code == "CST-04"
    for twin in ("cst-04", " CST-04", "Cst-04 "):
        with pytest.raises(DuplicateDealershipCodeError):
            dealership_repository.create(_dealership(twin, name="Twin"))
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.get_by_code("NOPE")
    changed = _dealership("cst-04", name="Renamed")
    dealership_repository.update(changed)  # typed in any case
    assert dealership_repository.get_by_code("CST-04").name == "Renamed"


def test_a_legacy_lowercase_dealership_code_is_still_found_updated_and_blocks_its_twin():
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO dealerships (code, name, region, city) VALUES ('old-1', 'Old', 'Metro', 'Ankara')")
    assert dealership_repository.get_by_code("OLD-1").code == "old-1"
    with pytest.raises(DuplicateDealershipCodeError):
        dealership_repository.create(_dealership("OLD-1"))
    row = dealership_repository.get_by_code("old-1")
    row.city = "Bursa"
    dealership_repository.update(row)
    assert dealership_repository.get_by_code("old-1").city == "Bursa"
    row.is_active = False
    dealership_repository.update(_dealership("OLD-1", name="Old", region="Metro", city="Bursa", is_active=False))
    assert not dealership_repository.get_by_code("old-1").is_active


# --- 5. attendance vs. activity ------------------------------------------------------

def test_inactive_employees_cannot_check_in():
    employee_repository.create(_employee(is_active=False))
    with pytest.raises(EmployeeInactiveError):
        attendance_repository.check_in("B-1")
    assert attendance_repository.list_open() == []


def test_deactivating_a_clocked_in_employee_closes_the_shift():
    employee_repository.create(_employee())
    attendance_repository.check_in("B-1", note="morning")
    staff = employee_repository.get_by_badge_id("B-1")
    staff.is_active = False
    employee_repository.update(staff)
    assert attendance_repository.list_open() == []
    [row] = attendance_repository.list_roster()
    assert row["status"] == "Checked out" and row["check_out_at"] and row["hours"] is not None
    with connection.connection_scope() as conn:
        note = conn.execute("SELECT note FROM attendance_records").fetchone()[0]
    assert note.startswith("morning") and "Auto check-out" in note
    # switching back on does not reopen anything, and they can clock in again
    staff.is_active = True
    employee_repository.update(staff)
    attendance_repository.check_in("B-1")
    assert len(attendance_repository.list_open()) == 1


def test_updating_an_already_inactive_employee_does_not_touch_attendance():
    employee_repository.create(_employee())
    attendance_repository.check_in("B-1")
    attendance_repository.check_out("B-1")
    staff = employee_repository.get_by_badge_id("B-1")
    staff.is_active = False
    employee_repository.update(staff)
    with connection.connection_scope() as conn:
        note = conn.execute("SELECT note FROM attendance_records").fetchone()[0]
    assert note is None


# --- 10. deleting an employee removes the linked account ------------------------------

def test_deleting_an_employee_removes_their_account():
    accounts.create_first_admin("A-1", "Erol", "482913")
    employee_repository.create(_employee("B-2"))
    accounts.create_account("B-2", "cashier", "5831")
    employee_repository.delete("B-2")
    with pytest.raises(AccountNotFoundError):
        accounts.get("B-2")
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.delete("B-2")


def test_an_admin_cannot_delete_or_deactivate_their_own_employee_record():
    session = accounts.create_first_admin("A-1", "Erol", "482913")
    with pytest.raises(SelfActionError):
        employee_repository.delete("a-1", by=session.actor)
