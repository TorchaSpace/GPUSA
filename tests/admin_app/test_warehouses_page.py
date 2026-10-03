"""Offscreen GUI tests for admin_app's Warehouses page."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import (
    attendance_repository,
    dealership_repository,
    employee_repository,
    product_repository,
    shipment_repository,
    stock_repository,
    warehouse_repository,
)
from shared.models import UNASSIGNED, Dealership, Employee, Product, StockLocation, Warehouse

WH1, WH2 = StockLocation.warehouse("WH-01"), StockLocation.warehouse("WH-02")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def page(qapp):
    warehouse_repository.create(Warehouse(code="WH-01", name="İstanbul Merkez", city="Tuzla", capacity_units=500, docks=8))
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze", city="Gebze"))
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("BOX", "Carton", 40, 300, 10))
    product_repository.create(Product("TAPE", "Tape", 5, 50, 10))
    stock_repository.transfer(UNASSIGNED, WH1, "BOX", 280)
    stock_repository.receive(WH2, "TAPE", 7, "PO-9")
    employee_repository.create(Employee("B-1", "Murat", "Operations", "Warehouse", "istanbul merkez"))
    employee_repository.create(Employee("B-2", "Ayşe", "Logistics", "Warehouse", "WH-02"))
    attendance_repository.check_in("B-1")
    from admin_app.gui.pages.warehouses_page import WarehousesPage

    widget = WarehousesPage()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _texts(table, column):
    return [table.item(r, column).text() for r in range(table.rowCount())]


def test_cards_show_real_capacity_status_and_staff(page):
    first, second = page._cards
    assert first.percent_label.text() == "56%" and first.status_label.text() == "Operational"
    assert first.units_label.text() == "280 / 500 units"
    assert first.shift_label.text() == "1 / 1"  # matched by name, ignoring case
    assert second.status_label.text() == "Capacity not set" and second.shift_label.text() == "0 / 1"
    assert page._units_card._value_label.text() == "287"
    assert page._unassigned_card._value_label.text() == "70"  # 20 cartons + 50 tape not placed


def test_near_capacity_at_the_85_percent_threshold(page):
    stock_repository.transfer(UNASSIGNED, WH1, "BOX", 20)  # 300 / 500
    stock_repository.receive(WH1, "TAPE", 124)  # 424 / 500 = 84.8%
    page.reload()
    assert page._cards[0].status_label.text() == "Operational"
    stock_repository.receive(WH1, "TAPE", 1)  # 425 / 500 = 85%
    page.reload()
    assert page._cards[0].status_label.text() == "Near capacity"
    assert page._near_card._value_label.text() == "1"


def test_clicking_a_card_filters_the_log_and_toggles_off(page):
    assert set(_texts(page._moves_table, 5)) == {"WH-01", "WH-02"}
    page.select_site("WH-02")
    assert _texts(page._moves_table, 5) == ["WH-02"]
    assert _texts(page._moves_table, 7) == ["PO-9"]
    page.set_direction("Outbound")
    assert page._moves_table.rowCount() == 0
    page.set_direction("All")
    page.select_site("WH-02")  # again: cleared
    assert page._site is None and page._moves_table.rowCount() == 2


def test_workforce_tab_lists_each_warehouses_people(page):
    page.set_tab("workforce")
    assert sorted(zip(_texts(page._people_table, 1), _texts(page._people_table, 3))) == [("Ayşe", "WH-02"), ("Murat", "WH-01")]
    page.select_site("WH-01")
    assert _texts(page._people_table, 4) == ["Present"]


def test_stock_tab_accounts_for_every_unit(page):
    shipment = shipment_repository.create("WH-01 · İstanbul Merkez", "001", "X", datetime.now() + timedelta(hours=2),
                                          [("BOX", 30)], origin_code="WH-01")
    shipment_repository.dispatch(shipment.id)
    page.reload()
    page.set_tab("stock")
    table = page._stock_table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["Product", "SKU", "WH-01", "WH-02", "Dealerships", "Unassigned", "On the road", "Company total"]
    box = _texts(table, 1).index("BOX")
    assert [table.item(box, c).text() for c in range(2, 8)] == ["250", "0", "0", "20", "30", "300"]


def test_place_all_unassigned(page):
    assert page._banner.isVisible()
    assert page.place_all_unassigned("WH-02") == 70
    assert not page._banner.isVisible()
    assert stock_repository.quantity_at(WH2, "TAPE") == 57


def test_move_popup_moves_and_counts(page):
    page._open_move()
    popup = page._move_popup
    popup._product_input.setCurrentIndex(popup._product_input.findData("BOX"))
    popup._select(popup._from_input, WH1)
    popup._select(popup._to_input, StockLocation.dealership("001"))
    popup._qty_input.setValue(5)
    popup._save()
    assert stock_repository.quantity_at(StockLocation.dealership("001"), "BOX") == 5
    popup._qty_input.setValue(9999)
    popup._save()
    assert "only 275" in popup._error.text()
    popup._count_radio.setChecked(True)
    popup._qty_input.setValue(270)
    popup._save()
    assert stock_repository.quantity_at(WH1, "BOX") == 270
    popup.close()


def test_add_edit_and_delete_guard(page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page._form.refresh_content(None)
    page._form._code_input.setText("WH-03")
    page._form._name_input.setText("İzmir")
    page._form._capacity_input.setText("1.000")
    page._save_form()
    assert warehouse_repository.get_by_code("WH-03").capacity_units == 1000
    assert len(page._cards) == 3

    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    page.select_site("WH-01")
    page._delete_selected()  # holds stock
    assert warnings and "still holds" in warnings[0]
    page.select_site("WH-03")
    page._delete_selected()
    assert [w.code for w in warehouse_repository.list_all()] == ["WH-01", "WH-02"]
