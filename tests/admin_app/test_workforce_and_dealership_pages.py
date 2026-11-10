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

    employee_repository.create(Employee("B-1", "Elena Varga", "Operations", "Warehouse", "Merkez Depo", title="Driver",
                                        national_id="10000000146"))
    employee_repository.create(Employee("S-1", "Su Kaya", "Sales & service", "Dealership", "CST-04"))
    page = WorkforcePage()
    page.show()
    pump(qapp)
    yield page
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


# --- workforce: Admin only watches ------------------------------------------------------

def test_admin_workforce_has_no_add_or_edit(workforce):
    view = workforce.view
    assert view.add_button is None and view.edit_button is None
    assert not hasattr(view, "badge_input")  # no check-in box either
    texts = [b.text() for b in workforce.findChildren(type(view.tab_buttons["today"]))]
    assert tr("staff.people.add") not in texts


def test_admin_sees_every_place_and_masks_the_national_id(workforce):
    view = workforce.view
    view.show_tab(2)
    names = [view.people_table.item(r, 0).text() for r in range(view.people_table.rowCount())]
    assert {"Elena Varga", "Su Kaya", "Erol"} <= set(names)
    row = names.index("Elena Varga")
    assert view.people_table.item(row, 2).text() == "100******46"
    index = next(i for i in range(view.place_filter.count())
                 if view.place_filter.itemData(i) is not None and view.place_filter.itemData(i).code == "CST-04")
    view.place_filter.setCurrentIndex(index)
    assert [view.people_table.item(r, 0).text() for r in range(view.people_table.rowCount())] == ["Su Kaya"]


def test_admin_sees_who_came_and_left(workforce):
    attendance_repository.check_in("B-1")
    attendance_repository.check_out("B-1")
    attendance_repository.check_in("S-1")
    workforce.reload()
    view = workforce.view
    view.show_tab(0)
    statuses = {view.today_table.item(r, 0).text(): view.today_table.item(r, 4).text()
                for r in range(view.today_table.rowCount())}
    assert statuses["Su Kaya"] == tr("staff.state.in") and statuses["Elena Varga"] == tr("staff.state.out")
    view.show_tab(1)
    assert view.history_table.rowCount() == 2
    assert tr("staff.history.still_in") in [view.history_table.item(r, 6).text() for r in range(2)]


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
    assert dealerships._detail_name.text() == "New Dealer" and "NEW-1" in dealerships._detail_code.text()


def test_duplicate_dealership_code_keeps_the_popup_open(dealerships, qapp):
    dealerships._open_add_popup()
    popup = dealerships._popup
    _fill_dealership(popup, code="cst-04", name="Clone")
    popup.accept()
    pump(qapp)
    assert popup.isVisible() and "already exists" in popup._error.text() and popup._name_input.text() == "Clone"


def test_editing_a_dealership_refreshes_the_detail_panel(dealerships, qapp):
    assert dealerships.select_code("CST-04")
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
    dealerships.select_code("CST-04")
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
    assert window._dealerships_page.card_count() == 2
    view = window._workforce_page.view
    view.show_tab(2)
    assert "Joined Later" in [view.people_table.item(r, 0).text() for r in range(view.people_table.rowCount())]
    window.close()


# --- dealership cards ---------------------------------------------------------------------

def test_cards_show_real_revenue_staff_and_filter(dealerships, qapp):
    from database import product_repository, stock_repository, transaction_repository
    from shared.models import LineItem, Product, StockLocation, Transaction

    dealership_repository.create(Dealership(code="MET-1", name="Metro Shop", region="Metro", city="Bursa"))
    product_repository.create(Product("W", "Widget", 50, 0, 2))
    shop = StockLocation.dealership("CST-04")
    stock_repository.receive(shop, "W", 10)
    transaction_repository.finalize_transaction(Transaction(items=[LineItem("W", "Widget", 50, 3)]), shop)
    employee_repository.create(Employee("S-1", "Sara Shop", "Sales & service", "Dealership", "CST-04"))
    dealerships.reload()
    pump(qapp)

    cards = {c.dealership.code: c for c in dealerships._grid.cards()}
    assert "150" in cards["CST-04"].revenue_label.text()
    assert dealerships._visible()[0].code == "CST-04"  # sorted by revenue
    assert dealerships.select_code("CST-04") and dealerships._detail.isVisible()
    staff = [label.text() for label in dealerships._detail._staff_box.findChildren(type(dealerships._detail_name))]
    assert "Sara Shop" in staff
    stock = [label.text() for label in dealerships._detail._stock_box.findChildren(type(dealerships._detail_name))]
    assert "Widget" in stock and "7 / 2" in stock

    dealerships._region_input.setCurrentIndex(dealerships._region_input.findData("Metro"))
    assert [d.code for d in dealerships._visible()] == ["MET-1"]
    assert dealerships.selected_dealership() is None and not dealerships._detail.isVisible()
    dealerships._region_input.setCurrentIndex(0)
    dealerships._search_input.setText("bursa")
    assert dealerships.card_count() == 1


def test_clicking_an_open_card_closes_it(dealerships, qapp):
    card = dealerships._grid.cards()[0]
    card.clicked.emit(card.dealership.code)
    assert dealerships.selected_dealership().code == "CST-04"
    card.clicked.emit(card.dealership.code)
    assert dealerships.selected_dealership() is None


def test_head_office_people_and_account_titles_read_in_the_screens_language(qapp):
    from shared import i18n
    from shared.gui_kit import staff_view

    hq = {"location_type": "Warehouse", "location_name": "HQ"}
    assert staff_view._place_text(hq) == "Head office"
    assert staff_view._place_text({"location_type": "Warehouse", "location_name": "Merkez Depo"}).endswith("Merkez Depo")
    assert staff_view._title_text("Driver") == "Driver" and staff_view._title_text(None) == ""
    i18n.set_language("tr")
    try:
        assert staff_view._place_text(hq) == "Merkez"
        assert staff_view._title_text("Depot manager") == "Depo müdürü"
        assert staff_view._title_text("Cashier") == "Kasiyer"
    finally:
        i18n.set_language("en")
