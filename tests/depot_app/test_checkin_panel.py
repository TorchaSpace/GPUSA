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


@pytest.fixture
def site_panel(qapp):
    from database import warehouse_repository
    from depot_app.gui.checkin_panel import CheckInPanel
    from shared.models import Warehouse

    warehouse = warehouse_repository.create(Warehouse(code="WH-01", name="Merkez Depo"))
    warehouse_repository.create(Warehouse(code="WH-02", name="Izmir Depo"))
    from database import dealership_repository
    from shared.models import Dealership

    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    employee_repository.create(Employee("B-1", "Local Worker", "Operations", "Warehouse", "WH-01"))
    employee_repository.create(Employee("B-2", "Izmir Worker", "Operations", "Warehouse", "WH-02"))
    employee_repository.create(Employee("B-3", "Shop Cashier", "Sales & service", "Dealership", "Harbor Point"))
    widget = CheckInPanel(warehouse)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


@pytest.mark.parametrize("badge", ["B-2", "B-3"])
def test_other_site_staff_cannot_check_in_here(site_panel, badge):
    site_panel._badge_input.setText(badge)
    site_panel._on_check_in()
    assert attendance_repository.list_open() == []
    assert badge in site_panel._message.text()


def test_this_sites_staff_can_check_in_and_out(site_panel):
    site_panel._badge_input.setText("B-1")
    site_panel._on_check_in()
    assert [r["badge_id"] for r in attendance_repository.list_open()] == ["B-1"]
    site_panel._badge_input.setText("B-1")
    site_panel._on_check_out()
    assert attendance_repository.list_open() == []


def test_other_site_staff_cannot_check_out_here(site_panel):
    attendance_repository.check_in("B-2")  # punched in at their own site (Console, unscoped)
    site_panel._badge_input.setText("B-2")
    site_panel._on_check_out()
    assert [r["badge_id"] for r in attendance_repository.list_open()] == ["B-2"]


def test_operator_badge_must_work_at_this_warehouse(site_panel):
    attendance_repository.check_in("B-2")
    from database.exceptions import EmployeeOtherSiteError

    with pytest.raises(EmployeeOtherSiteError):
        attendance_repository.operator_actor("B-2", warehouse_code="WH-01")
    assert attendance_repository.operator_actor("B-2", warehouse_code="WH-02").badge_id == "B-2"
