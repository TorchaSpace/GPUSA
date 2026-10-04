"""Offscreen tests: the Floor's Check-in Log (badge punches, roster, today's punches)."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import attendance_repository, employee_repository
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def panel(qapp):
    employee_repository.create(Employee("B-7", "Elena Varga", "Operations", "Warehouse", "Merkez Depo"))
    employee_repository.create(Employee("B-9", "Gone Away", "Logistics", "Warehouse", "Merkez Depo", is_active=False))
    from depot_app.gui.checkin_panel import CheckInPanel

    widget = CheckInPanel()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_roster_shows_active_staff_and_the_count(panel):
    assert panel._count.text() == "0 / 1 on floor"  # the deactivated employee is not listed


def test_check_in_and_out_by_badge(panel):
    emitted = []
    panel.attendance_changed.connect(lambda: emitted.append(1))
    panel._badge_input.setText("b-7")
    panel._on_check_in()
    assert [r["badge_id"] for r in attendance_repository.list_open()] == ["B-7"]
    assert panel._count.text() == "1 / 1 on floor"
    assert "checked in" in panel._message.text()
    assert panel._badge_input.text() == ""
    panel._badge_input.setText("B-7")
    panel._on_check_out()
    assert attendance_repository.list_open() == []
    assert "checked out" in panel._message.text()
    assert len(emitted) == 2


def test_unknown_badge_and_empty_badge_are_messages_not_crashes(panel):
    panel._on_check_in()
    assert "badge" in panel._message.text().lower()
    panel._badge_input.setText("NOPE")
    panel._on_check_in()
    assert panel._message.isVisibleTo(panel)
    assert attendance_repository.list_open() == []


def test_checking_in_twice_is_refused(panel):
    panel._badge_input.setText("B-7")
    panel._on_check_in()
    panel._badge_input.setText("B-7")
    panel._on_check_in()
    assert len(attendance_repository.list_open()) == 1
