"""Offscreen GUI tests for depot_app's Console > Shipments page."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships
from database import stock_repository, warehouse_repository
from shared.models import Dealership, Product, StockLocation, Warehouse

WH = StockLocation.warehouse("WH-01")

SITE = "WH-01 · Test"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def page(qapp):
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    dealership_repository.create(Dealership(code="002", name="Closed Branch", region="Metro", city="X", is_active=False))
    product_repository.create(Product("BOX-2218", "Carton", 40, 300, 10))
    product_repository.create(Product("PLT-4410", "Pallet wrap", 900, 50, 10))
    warehouse_repository.create(Warehouse(code="WH-01", name="Test"))
    stock_repository.place_all_unassigned(WH)
    from depot_app.gui.shipments_page import ShipmentsPage

    widget = ShipmentsPage(SITE, "WH-01")
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_only_active_dealerships_are_offered(page):
    codes = [page._dest_input.itemData(i) for i in range(page._dest_input.count())]
    assert codes == ["001"]


def test_create_and_dispatch_from_the_form(page):
    page._carrier_input.setText("Ridgeline Freight")
    page._product_input.setCurrentIndex(page._product_input.findData("BOX-2218"))
    page._qty_input.setValue(10)
    page._add_line()
    page._add_line()  # same product again - merged
    page._dispatch_now.setChecked(True)
    page._create()

    [shipment] = ships.list_shipments()
    assert (shipment.origin, shipment.origin_code, shipment.status, shipment.carrier) == (
        SITE, "WH-01", "in_transit", "Ridgeline Freight")
    assert stock_repository.quantity_at(WH, "BOX-2218") == 280  # loaded off this warehouse
    assert [(l.product_barcode, l.expected_qty) for l in shipment.lines] == [("BOX-2218", 20)]
    assert page._draft_lines == []
    assert "dispatched" in page._message.text()
    assert page.selected().id == shipment.id


def test_form_errors_are_shown_not_raised(page):
    page._carrier_input.setText("Ridgeline")
    page._create()  # no lines
    assert "at least one product" in page._form_error.text()
    assert ships.list_shipments() == []


def test_dispatch_update_eta_cancel_from_the_detail(page):
    eta = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)  # the editor shows whole minutes
    shipment = ships.create(SITE, "001", "Ridgeline", eta, [("BOX-2218", 5)], origin_code="WH-01")
    page.reload()
    page.select(shipment.id)
    assert page._dispatch_button.isEnabled()

    page._act("dispatch")
    assert ships.get(shipment.id).status == "in_transit"
    assert not page._dispatch_button.isEnabled()

    page._new_eta_input.setDateTime(page._new_eta_input.dateTime().addSecs(3 * 3600))
    page._act("eta")
    assert page._table.item(0, 7).text().startswith("+3h")  # late column

    page._act("cancel")
    assert ships.get(shipment.id).status == "cancelled"
    assert not page._cancel_button.isEnabled()


def test_shows_only_this_warehouses_shipments_and_receipt_reports(page):
    mine = ships.create(SITE, "001", "Ridgeline", datetime.now() + timedelta(hours=2), [("BOX-2218", 24)],
                        origin_code="WH-01")
    legacy = ships.create(SITE, "001", "Old", datetime.now() + timedelta(hours=3), [("BOX-2218", 1)])  # site text only
    ships.create("WH-02", "001", "Other", datetime.now(), [("BOX-2218", 1)])
    ships.dispatch(mine.id)
    ships.complete_receipt(mine.id, {"BOX-2218": 20}, "crushed")
    page.reload()

    assert sorted(s.id for s in page.shipments()) == [mine.id, legacy.id]
    row = [s.id for s in page.shipments()].index(mine.id)
    assert page._table.item(row, 6).text() == "Delivered · report"
    page.select(mine.id)
    assert page._detail_lines.item(0, 3).text() == "-4"
    assert "crushed" in page._detail_meta.text()


def test_product_picker_shows_what_is_on_hand_here(page):
    labels = [page._product_input.itemText(i) for i in range(page._product_input.count())]
    assert "BOX-2218 · Carton · 300 here" in labels


def test_dispatch_is_refused_when_this_warehouse_is_short(page):
    shipment = ships.create(SITE, "001", "Ridgeline", datetime.now() + timedelta(hours=2), [("PLT-4410", 51)],
                            origin_code="WH-01")
    page.reload()
    page.select(shipment.id)
    page._act("dispatch")
    assert ships.get(shipment.id).status == "scheduled"
    assert "only 50 of PLT-4410" in page._message.text()


def test_create_and_dispatch_now_keeps_the_shipment_when_stock_is_short(page):
    page._carrier_input.setText("Ridgeline")
    page._product_input.setCurrentIndex(page._product_input.findData("PLT-4410"))
    page._qty_input.setValue(60)
    page._add_line()
    page._dispatch_now.setChecked(True)
    page._create()
    [shipment] = ships.list_shipments()
    assert shipment.status == "scheduled"
    assert "Not dispatched" in page._message.text() and page._draft_lines == []


def test_an_eta_before_the_departure_is_reported_not_raised(page):
    shipment = ships.create(SITE, "001", "Ridgeline", datetime.now() + timedelta(hours=2), [("BOX-2218", 5)],
                            origin_code="WH-01")
    page.reload()
    page.select(shipment.id)
    page._act("dispatch")

    page._new_eta_input.setDateTime(page._new_eta_input.dateTime().addDays(-3))  # three days before it left
    page._act("eta")  # used to raise ValueError out of the click handler

    assert "Couldn't update" in page._message.text() and "departure" in page._message.text()
    assert ships.get(shipment.id).eta == shipment.eta


def test_a_past_eta_in_the_new_shipment_form_is_a_form_error(page):
    page._carrier_input.setText("Ridgeline")
    page._eta_input.setDateTime(page._eta_input.dateTime().addDays(-2))
    page._product_input.setCurrentIndex(page._product_input.findData("BOX-2218"))
    page._add_line()
    page._create()
    assert "before now" in page._form_error.text()
    assert ships.list_shipments() == []


def test_deactivated_products_are_not_offered_for_a_shipment(page):
    product_repository.set_active("PLT-4410", False)
    page.reload_choices()
    codes = [page._product_input.itemData(i) for i in range(page._product_input.count())]
    assert "PLT-4410" not in codes and "BOX-2218" in codes
