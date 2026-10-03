"""Regression tests for database.employee_repository - mirrors
test_dealership_repository.py's shape/fixture pattern.
"""

from __future__ import annotations

import pytest

import database.connection as connection
from database import employee_repository
from database.exceptions import DuplicateBadgeIdError, EmployeeNotFoundError
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _make_employee(badge_id: str = "EMP-1040", **overrides) -> Employee:
    fields = dict(
        badge_id=badge_id,
        name="Elena Varga",
        title="Forklift operator",
        role="Operations",
        location_type="Warehouse",
        location_name="İstanbul Merkez",
        is_active=True,
    )
    fields.update(overrides)
    return Employee(**fields)


def test_create_then_get_by_badge_id_roundtrips():
    employee = _make_employee()

    employee_repository.create(employee)
    fetched = employee_repository.get_by_badge_id(employee.badge_id)

    assert fetched.badge_id == employee.badge_id
    assert fetched.name == employee.name
    assert fetched.title == employee.title
    assert fetched.role == employee.role
    assert fetched.location_type == employee.location_type
    assert fetched.location_name == employee.location_name
    assert fetched.is_active is True
    assert fetched.id is not None


def test_create_duplicate_badge_id_raises():
    employee_repository.create(_make_employee())

    with pytest.raises(DuplicateBadgeIdError):
        employee_repository.create(_make_employee(name="A Different Name"))


def test_create_invalid_role_raises():
    with pytest.raises(ValueError):
        employee_repository.create(_make_employee(role="Nowhere"))


def test_create_invalid_location_type_raises():
    with pytest.raises(ValueError):
        employee_repository.create(_make_employee(location_type="Nowhere"))


def test_update_invalid_role_raises():
    employee_repository.create(_make_employee())

    with pytest.raises(ValueError):
        employee_repository.update(_make_employee(role="Nowhere"))


def test_get_by_badge_id_missing_raises():
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.get_by_badge_id("does-not-exist")


def test_list_all_orders_by_name():
    employee_repository.create(_make_employee(badge_id="EMP-2", name="Bravo Person"))
    employee_repository.create(_make_employee(badge_id="EMP-1", name="Alpha Person"))

    result = employee_repository.list_all()

    assert [e.name for e in result] == ["Alpha Person", "Bravo Person"]


def test_update_changes_fields():
    original = _make_employee()
    employee_repository.create(original)

    employee_repository.update(
        _make_employee(
            name="Renamed Person",
            title="Dock supervisor",
            role="Logistics",
            location_type="Dealership",
            location_name="Harbor Point Equipment",
            is_active=False,
        )
    )

    fetched = employee_repository.get_by_badge_id(original.badge_id)
    assert fetched.name == "Renamed Person"
    assert fetched.title == "Dock supervisor"
    assert fetched.role == "Logistics"
    assert fetched.location_type == "Dealership"
    assert fetched.location_name == "Harbor Point Equipment"
    assert fetched.is_active is False


def test_update_missing_raises():
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.update(_make_employee(badge_id="does-not-exist"))


def test_delete_removes_employee():
    employee = _make_employee()
    employee_repository.create(employee)

    employee_repository.delete(employee.badge_id)

    with pytest.raises(EmployeeNotFoundError):
        employee_repository.get_by_badge_id(employee.badge_id)


def test_delete_missing_raises():
    with pytest.raises(EmployeeNotFoundError):
        employee_repository.delete("does-not-exist")


def test_delete_with_attendance_history_raises_value_error():
    from database import attendance_repository

    employee = _make_employee()
    employee_repository.create(employee)
    attendance_repository.check_in(employee.badge_id)

    with pytest.raises(ValueError):
        employee_repository.delete(employee.badge_id)
