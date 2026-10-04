"""Offscreen tests: Workforce and Dealerships pages - popups that keep a
rejected save open, pick-list locations, fresh detail panels, reload on show,
local times and the delete warning."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import (
    account_repository,
    attendance_repository,
    dealership_repository,
    employee_repository,
    warehouse_repository,
)
from shared import current_session
from shared.formatting import local_clock_text
from shared.i18n import tr
from shared.models import Dealership, Employee, Warehouse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    warehouse_repository.create(Warehouse(code="WH-01", name="Merkez Depo", city="Tuzla"))
    dealership_repository.create(Dealership(code="CST-04", name="Coastal Parts", region="Coastal", city="Izmir"))
    current_session.set(account_repository.create_first_admin("A-1", "Erol", "482913"))
    yield
    current_session.clear()


@pytest.fixture
def workforce(qapp, world):
    from admin_app.gui.pages.workforce_page import WorkforcePage

    employee_repository.create(Employee("B-1", "Elena Varga", "Operations", "Warehouse", "Merkez Depo", title="Driver"))
    page = WorkforcePage()
    page.show()
    pump(qapp)
    yield page
    page._popup.close()
    page.close()


@pytest.fixture
def dealerships(qapp, world):
    from admin_app.gui.pages.dealerships_page import DealershipsPage

    page = DealershipsPage()
    page.show()
    pump(qapp)
    yield page
    page._popup.close()
    page.close()


def _detail_texts(page) -> list[str]:
    texts = []
    layout = page._detail_rows_container
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget is not None:
            texts.extend(label.text() for label in widget.findChildren(type(page._detail_name)))
    return texts


def _fill_employee(popup, badge="B-2", name="Zeynep Acar", location="Merkez Depo"):
    popup._badge_input.setText(badge)
    popup._name_input.setText(name)
    popup._location_name_input.setEditText(location)


# --- employee popup --------------------------------------------------------------------

def test_employee_popup_rejects_empty_name_and_keeps_the_typed_data(workforce, qapp):
    workforce._open_add_popup()
    popup = workforce._popup
    _fill_employee(popup, name="  ")
    popup.accept()
    pump(qapp)
    assert popup.isVisible() and popup._error.isVisible() and popup._error.text()
    assert popup._badge_input.text() == "B-2" and popup._location_name_input.currentText() == "Merkez Depo"
    assert sorted(e.badge_id for e in employee_repository.list_all()) == ["A-1", "B-1"]  # the signed-in admin is an employee too


def test_employee_popup_rejects_empty_badge_and_huge_names(workforce, qapp):
    workforce._open_add_popup()
    popup = workforce._popup
    _fill_employee(popup, badge="   ")
    popup.accept()
    assert popup.isVisible() and popup._error.text()
    _fill_employee(popup, name="x" * 10_000)
    popup.accept()
    assert popup.isVisible() and popup._error.text()
    assert len(employee_repository.list_all()) == 2  # B-1 and the signed-in admin A-1


def test_employee_location_is_a_pick_list_of_existing_places(workforce, qapp):
    workforce._open_add_popup()
    box = workforce._popup._location_name_input
    assert box.isEditable()
    assert [box.itemText(i) for i in range(box.count())] == ["Merkez Depo"]  # warehouses for the Warehouse type
    workforce._popup._location_type_input.setCurrentText("Dealership")
    assert [box.itemText(i) for i in range(box.count())] == ["Coastal Parts"]
    assert box.currentText() == ""  # a Warehouse name doesn't linger on a Dealership employee


def test_employee_popup_refuses_a_place_that_does_not_exist(workforce, qapp):
    workforce._open_add_popup()
    popup = workforce._popup
    _fill_employee(popup, location="Atlantis")
    popup.accept()
    assert popup.isVisible() and popup._error.text()
    assert popup._location_name_input.currentText() == "Atlantis"  # typed text kept
    assert len(employee_repository.list_all()) == 2  # B-1 and the signed-in admin A-1


def test_employee_popup_saves_a_valid_entry_and_normalises_the_badge(workforce, qapp):
    workforce._open_add_popup()
    popup = workforce._popup
    _fill_employee(popup, badge=" z-2 ", location="merkez depo")
    popup.accept()
    pump(qapp)
    assert not popup.isVisible()
    assert employee_repository.get_by_badge_id("Z-2").name == "Zeynep Acar"
    assert workforce._detail_name.text() == "Zeynep Acar"  # the saved person is selected, detail fresh


def test_a_duplicate_badge_brings_the_popup_back_with_everything_typed(workforce, qapp):
    workforce._open_add_popup()
    popup = workforce._popup
    _fill_employee(popup, badge="b-1", name="Someone Else")
    popup.accept()  # passes the form's own checks; the repository then says duplicate
    pump(qapp)
    assert popup.isVisible() and "already exists" in popup._error.text()
    assert popup._name_input.text() == "Someone Else" and popup._badge_input.text() == "b-1"
    assert employee_repository.get_by_badge_id("B-1").name == "Elena Varga"


def test_editing_an_employee_with_a_legacy_free_text_place_is_allowed(workforce, qapp):
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                     "VALUES ('OLD-1', 'Old Timer', 'Operations', 'Warehouse', 'Closed Depot')")
    workforce.reload()
    assert workforce._table.select_badge("OLD-1")
    workforce._open_edit_popup()
    popup = workforce._popup
    box = popup._location_name_input
    assert "Closed Depot" in [box.itemText(i) for i in range(box.count())]
    popup._name_input.setText("Old Timer Jr")
    popup.accept()
    pump(qapp)
    assert not popup.isVisible() and employee_repository.get_by_badge_id("OLD-1").name == "Old Timer Jr"


def test_saving_an_edit_refreshes_the_detail_panel(workforce, qapp):
    assert workforce._table.select_badge("B-1")
    assert workforce._detail_name.text() == "Elena Varga"
    workforce._open_edit_popup()
    workforce._popup._name_input.setText("Elena V. Varga")
    workforce._popup.accept()
    pump(qapp)
    assert workforce._detail_name.text() == "Elena V. Varga"
    assert workforce._table.selected_row()["name"] == "Elena V. Varga"


# --- delete ----------------------------------------------------------------------------

def test_delete_confirmation_warns_the_account_goes_too(workforce, monkeypatch, qapp):
    from PySide6.QtWidgets import QMessageBox

    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", question)
    workforce._table.select_badge("B-1")
    workforce._delete_selected()
    assert asked == [tr("admin.workforce.delete_confirm").format(name="Elena Varga")]
    assert employee_repository.get_by_badge_id("B-1")  # declined: nothing deleted

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    workforce._delete_selected()
    pump(qapp)
    assert [e.badge_id for e in employee_repository.list_all()] == ["A-1"]  # only the signed-in admin is left
    assert workforce._detail_name.text() == "No employee selected"


# --- times and flags --------------------------------------------------------------------

def test_detail_shows_check_times_in_local_time(workforce, qapp):
    attendance_repository.check_in("b-1")
    workforce.reload()
    workforce._table.select_badge("B-1")
    entry = workforce._table.selected_row()
    assert entry["status"] == "Present"
    texts = _detail_texts(workforce)
    assert local_clock_text(entry["check_in_at"]) in texts  # "HH:MM" local - not the UTC "...T07:01:46" text
    assert not any(t.endswith("Z") for t in texts)


def test_a_forgotten_open_shift_is_flagged_in_the_table(workforce, qapp):
    from datetime import datetime, timedelta, timezone

    from shared.formatting import to_db_timestamp

    old = to_db_timestamp(datetime.now(timezone.utc) - timedelta(hours=20))
    with connection.connection_scope() as conn:
        employee_id = conn.execute("SELECT id FROM employees WHERE badge_id = 'B-1'").fetchone()[0]
        conn.execute("INSERT INTO attendance_records (employee_id, check_in_at) VALUES (?, ?)", (employee_id, old))
    workforce.reload()
    model = workforce._table.model()
    row = next(r for r in range(model.rowCount()) if model.data(model.index(r, 0)) == "B-1")
    cell = model.data(model.index(row, 4))
    assert cell.startswith("Present") and tr("admin.workforce.long_open") in cell


# --- dealerships ------------------------------------------------------------------------

def _fill_dealership(popup, code="NEW-1", name="New Dealer", city="Ankara"):
    popup._code_input.setText(code)
    popup._name_input.setText(name)
    popup._city_input.setText(city)


def test_dealership_popup_rejects_empty_and_huge_input_and_stays_open(dealerships, qapp):
    dealerships._open_add_popup()
    popup = dealerships._popup
    for bad in ({"name": ""}, {"code": "  "}, {"name": "x" * 10_000}):
        _fill_dealership(popup, **bad)
        popup.accept()
        assert popup.isVisible() and popup._error.isVisible() and popup._error.text()
    assert popup._city_input.text() == "Ankara"  # typed data kept
    assert [d.code for d in dealership_repository.list_all()] == ["CST-04"]


def test_dealership_save_normalises_the_code_and_selects_it(dealerships, qapp):
    dealerships._open_add_popup()
    _fill_dealership(dealerships._popup, code=" new-1 ")
    dealerships._popup.accept()
    pump(qapp)
    assert dealership_repository.get_by_code("NEW-1").name == "New Dealer"
    assert dealerships._detail_name.text() == "New Dealer" and dealerships._detail_code.text() == "NEW-1"


def test_duplicate_dealership_code_keeps_the_popup_open(dealerships, qapp):
    dealerships._open_add_popup()
    popup = dealerships._popup
    _fill_dealership(popup, code="cst-04", name="Clone")
    popup.accept()
    pump(qapp)
    assert popup.isVisible() and "already exists" in popup._error.text() and popup._name_input.text() == "Clone"


def test_editing_a_dealership_refreshes_the_detail_panel(dealerships, qapp):
    assert dealerships._table.select_code("CST-04")
    assert dealerships._detail_name.text() == "Coastal Parts"
    dealerships._open_edit_popup()
    dealerships._popup._name_input.setText("Coastal Parts II")
    dealerships._popup._active_input.setChecked(False)
    dealerships._popup.accept()
    pump(qapp)
    assert dealerships._detail_name.text() == "Coastal Parts II"
    assert "Inactive" in _detail_texts(dealerships)


def test_deleting_a_dealership_clears_the_detail_panel(dealerships, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dealerships._table.select_code("CST-04")
    dealerships._delete_selected()
    pump(qapp)
    assert dealerships._detail_name.text() == "No dealership selected"


# --- MainWindow reloads these pages when shown ------------------------------------------

def test_main_window_reloads_dealerships_and_workforce_on_show(qapp, world):
    from admin_app.gui.main_window import MainWindow

    window = MainWindow()
    dealership_repository.create(Dealership(code="LATE-1", name="Added Elsewhere", region="Metro", city="Bursa"))
    employee_repository.create(Employee("LATE-2", "Joined Later", "Logistics", "Dealership", "Added Elsewhere"))
    window._show_page("dealerships")
    window._show_page("workforce")
    assert window._dealerships_page._table.model().rowCount() == 2
    assert window._workforce_page._table.model().rowCount() == 2
    window.close()
