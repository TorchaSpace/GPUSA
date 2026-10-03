"""Regression tests for database.attendance_repository - the atomic
badge check-in/check-out pattern, same BEGIN IMMEDIATE shape as
test_inventory_repository.py.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import database.connection as connection
from database import attendance_repository, employee_repository
from database.exceptions import (
    AlreadyCheckedInError,
    EmployeeNotFoundError,
    NoOpenAttendanceRecordError,
)
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def employee() -> Employee:
    emp = Employee(
        badge_id="EMP-1040",
        name="Elena Varga",
        title="Forklift operator",
        role="Operations",
        location_type="Warehouse",
        location_name="İstanbul Merkez",
    )
    employee_repository.create(emp)
    return emp


def test_check_in_unknown_badge_raises():
    with pytest.raises(EmployeeNotFoundError):
        attendance_repository.check_in("does-not-exist")


def test_check_in_then_appears_in_list_open(employee):
    attendance_repository.check_in(employee.badge_id)

    open_records = attendance_repository.list_open()

    assert len(open_records) == 1
    assert open_records[0]["badge_id"] == employee.badge_id


def test_check_in_twice_raises(employee):
    attendance_repository.check_in(employee.badge_id)

    with pytest.raises(AlreadyCheckedInError):
        attendance_repository.check_in(employee.badge_id)


def test_check_out_unknown_badge_raises():
    with pytest.raises(EmployeeNotFoundError):
        attendance_repository.check_out("does-not-exist")


def test_check_out_without_check_in_raises(employee):
    with pytest.raises(NoOpenAttendanceRecordError):
        attendance_repository.check_out(employee.badge_id)


def test_check_out_closes_the_open_record(employee):
    attendance_repository.check_in(employee.badge_id)

    attendance_repository.check_out(employee.badge_id)

    assert attendance_repository.list_open() == []


def test_check_in_after_check_out_succeeds_again(employee):
    attendance_repository.check_in(employee.badge_id)
    attendance_repository.check_out(employee.badge_id)

    attendance_repository.check_in(employee.badge_id)

    assert len(attendance_repository.list_open()) == 1


def test_list_roster_shows_off_with_no_record(employee):
    roster = attendance_repository.list_roster()

    assert len(roster) == 1
    assert roster[0]["badge_id"] == employee.badge_id
    assert roster[0]["status"] == "Off"
    assert roster[0]["hours"] is None


def test_list_roster_shows_present_after_check_in(employee):
    attendance_repository.check_in(employee.badge_id)

    roster = attendance_repository.list_roster()

    assert roster[0]["status"] == "Present"
    assert roster[0]["check_in_at"] is not None
    assert roster[0]["check_out_at"] is None
    assert roster[0]["hours"] is None


def test_list_roster_shows_checked_out_with_hours(employee):
    attendance_repository.check_in(employee.badge_id)
    attendance_repository.check_out(employee.badge_id)

    roster = attendance_repository.list_roster()

    assert roster[0]["status"] == "Checked out"
    assert roster[0]["hours"] is not None
    assert roster[0]["hours"] >= 0


def test_list_roster_for_other_date_shows_off(employee):
    attendance_repository.check_in(employee.badge_id)

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    roster = attendance_repository.list_roster(for_date=yesterday)

    assert roster[0]["status"] == "Off"
