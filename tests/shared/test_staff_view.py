"""Offscreen: the Staff screen (shared/gui_kit/staff_view.py) as the depot and the till use it -
add a person with a national ID, check them in and out, see the day and the history - and where it
sits in the depot Console and the till."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import account_repository, dealership_repository, employee_repository, warehouse_repository
from depot_app.theme import INDUSTRY_PALETTE
from shared import current_session
from shared.auth import AREA_DEPOT_CONSOLE, Session
from shared.i18n import tr
from shared.models import Dealership, Employee, Warehouse
from shared.staff import WAREHOUSE, StaffPlace

DEPOT = StaffPlace(WAREHOUSE, "WH-01", "Merkez")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def manager():
    warehouse_repository.create(Warehouse(code="WH-01", name="Merkez"))
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze"))
    employee_repository.create(Employee("M-1", "Mert", "Management", "Warehouse", "WH-01"))
    employee_repository.create(Employee("G-1", "Gül", "Operations", "Warehouse", "Gebze"))  # the other depot's
    account_repository.create_account("M-1", "depot_manager", "583920")
    session = Session(1, "M-1", "Mert", "depot_manager", "2026-11-08T06:00:00.000Z", "depot_console", "Depot WH-01")
    current_session.set(session)
    yield session
    current_session.clear()


@pytest.fixture
def view(qapp, manager):
    from shared.gui_kit.staff_view import StaffView

    widget = StaffView(INDUSTRY_PALETTE, place=DEPOT, can_manage=True, area=AREA_DEPOT_CONSOLE)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _column(table, col):
    return [table.item(r, col).text() for r in range(table.rowCount())]


def test_only_this_depots_people_are_listed(view):
    view.show_tab(2)
    assert _column(view.people_table, 0) == ["Mert"]


def test_add_a_person_with_a_national_id(view):
    dialog = view.form()
    assert dialog.badge_input.text() == "WH-01-0003"  # suggested, can be changed
    dialog.name_input.setText("  Ayşe   Yılmaz ")
    dialog.national_id_input.setText("10000000147")  # one digit wrong
    dialog.submit()
    assert dialog.error_label.isVisibleTo(dialog) and dialog.result() == 0
    dialog.national_id_input.setText("100 000 001 46")
    dialog.title_input.setText("Forklift")
    dialog.submit()
    assert dialog.result() == 1, dialog.error_label.text()
    dialog.deleteLater()
    saved = employee_repository.get_by_badge_id("WH-01-0003")
    assert (saved.name, saved.national_id, saved.location_type, saved.title) == (
        "Ayşe Yılmaz", "10000000146", "Warehouse", "Forklift")
    view.show_tab(2)
    row = _column(view.people_table, 0).index("Ayşe Yılmaz")
    assert view.people_table.item(row, 2).text() == "100******46"


def test_the_same_national_id_twice_is_refused(view):
    employee_repository.create(Employee("W-9", "Veli", "Operations", "Warehouse", "Merkez", national_id="10000000146"))
    dialog = view.form()
    dialog.name_input.setText("Başka Biri")
    dialog.national_id_input.setText("10000000146")
    dialog.submit()
    assert dialog.result() == 0 and "W-9" in dialog.error_label.text()
    dialog.deleteLater()


def test_check_in_and_out_and_see_it_today_and_in_the_history(view):
    view.badge_input.setText("m-1")
    view.check_in()
    assert "Mert" in view.punch_message.text()
    view.show_tab(0)
    assert _column(view.today_table, 3) == [tr("staff.state.in")]
    view.badge_input.setText("G-1")  # works at the other depot
    view.check_in()
    assert "G-1" in view.punch_message.text() and "WH-01" in view.punch_message.text()
    view.badge_input.setText("M-1")
    view.check_out()
    view.show_tab(0)
    assert _column(view.today_table, 3) == [tr("staff.state.out")]
    view.show_tab(1)
    assert view.history_table.rowCount() == 1
    assert _column(view.history_table, 2) == ["Mert"]
    assert view.person_filter.count() == 2  # everyone + Mert
    view.set_period("yesterday")
    assert view.history_table.rowCount() == 0 and view.history_summary.text() == tr("staff.history.empty")


def test_edit_keeps_the_saved_national_id(view):
    employee_repository.create(Employee("W-9", "Veli", "Operations", "Warehouse", "Merkez", national_id="10000000146"))
    dialog = view.form(employee_repository.get_by_badge_id("W-9"))
    assert not dialog.badge_input.isEnabled() and "100******46" in dialog.national_id_input.placeholderText()
    dialog.title_input.setText("Şef")
    dialog.active_input.setChecked(False)
    dialog.submit()
    assert dialog.result() == 1, dialog.error_label.text()
    dialog.deleteLater()
    saved = employee_repository.get_by_badge_id("W-9")
    assert (saved.title, saved.is_active, saved.national_id) == ("Şef", False, "10000000146")


def test_console_has_a_staff_page_for_its_warehouse(qapp, manager):
    from depot_app.gui.console_window import ConsoleWindow

    console = ConsoleWindow(warehouse_repository.get_by_code("WH-01"), manager)
    console._navigate("Staff")
    page = console._staff_page
    assert console._stack.currentWidget() is page and page._place.code == "WH-01"
    console.close()


def test_till_has_a_staff_page_for_its_dealership(qapp, monkeypatch):
    import pos_app.gui.main_window as mw

    dealership_repository.create(Dealership(code="CST-04", name="Coast", region="Coastal", city="Izmir"))
    employee_repository.create(Employee("S-1", "Su", "Sales & service", "Dealership", "Coast"))
    monkeypatch.setattr(mw, "load_dealership_identity",
                        lambda: {"code": "CST-04", "name": "Coast", "location_line": "Izmir"})
    window = mw.MainWindow(None)
    window._watcher.stop()
    window._navigate("staff")
    view = window._staff_page.view
    assert view is not None and view._place.code == "CST-04" and view.add_button is not None
    view.show_tab(2)
    assert _column(view.people_table, 0) == ["Su"]
    window.close()

    monkeypatch.setattr(mw, "load_dealership_identity", lambda: None)
    bare = mw.MainWindow(None)
    bare._watcher.stop()
    assert bare._staff_page.view is None
    bare.close()
