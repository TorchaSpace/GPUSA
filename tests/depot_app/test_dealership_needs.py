"""Offscreen GUI tests for the depot's Dealership needs panel on Shipments:
stock requests from the POS turn into a shipment draft or get declined, and
shops running low can be topped up from the same place."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships
from database import stock_repository, stock_request_repository as requests, warehouse_repository
from shared.models import Dealership, Product, StockLocation, Warehouse

WH = StockLocation.warehouse("WH-01")
SHELF = StockLocation.dealership("001")
SITE = "WH-01 · Test"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def page(qapp):
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    dealership_repository.create(Dealership(code="003", name="Metro Heavy", region="Metro", city="Columbus"))
    product_repository.create(Product("BOX-2218", "Carton", 40, 300, 10))
    product_repository.create(Product("PLT-4410", "Pallet wrap", 900, 50, 10))
    warehouse_repository.create(Warehouse(code="WH-01", name="Test"))
    stock_repository.place_all_unassigned(WH)
    stock_repository.transfer(WH, SHELF, "PLT-4410", 4)  # 4 <= 10: running low
    requests.create("001", "BOX-2218", 12, note="weekend")
    requests.create("001", "PLT-4410", 5)
    requests.create("003", "BOX-2218", 7)
    from depot_app.gui.shipments_page import ShipmentsPage

    widget = ShipmentsPage(SITE, "WH-01")
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _select(table, row):
    table.selectRow(row)


def test_open_requests_and_shortages_are_listed(page):
    needs = page.needs
    assert [r.number for r in needs.requests] == ["RQ-00001", "RQ-00002", "RQ-00003"]
    assert needs.requests_table.rowCount() == 3
    assert [(s.dealership_code, s.product_barcode) for s in needs.shortages] == [("001", "PLT-4410")]
    assert not needs.plan_button.isEnabled() and not needs.add_low_button.isEnabled()


def test_planning_a_request_brings_every_request_of_that_shop_and_links_them(page):
    _select(page.needs.requests_table, 0)
    page.needs.plan_selected_request()
    assert page._dest_input.currentData() == "001"
    assert sorted(page.draft()) == [("BOX-2218", 12), ("PLT-4410", 5)]

    page._carrier_input.setText("Ridgeline")
    page._create()
    [shipment] = ships.list_shipments()
    planned = [requests.get(i) for i in (1, 2)]
    assert {(r.status, r.shipment_id) for r in planned} == {("planned", shipment.id)}
    assert requests.get(3).status == "open"
    assert [r.number for r in page.needs.requests] == ["RQ-00003"]


def test_changing_the_destination_does_not_plan_the_requests(page):
    _select(page.needs.requests_table, 0)
    page.needs.plan_selected_request()
    page._dest_input.setCurrentIndex(page._dest_input.findData("003"))
    page._carrier_input.setText("Ridgeline")
    page._create()
    [shipment] = ships.list_shipments()
    assert shipment.dealership_code == "003"
    assert requests.get(1).status == "open"


def test_declining_a_request_keeps_the_reason(page):
    _select(page.needs.requests_table, 2)
    page.needs.reason_input.setText("Out of cartons this week")
    assert page.needs.decline_selected()
    declined = requests.get(3)
    assert (declined.status, declined.decision_note) == ("declined", "Out of cartons this week")
    assert "RQ-00003" in page.needs.message.text()
    assert len(page.needs.requests) == 2


def test_a_shortage_adds_a_suggested_line_to_the_draft(page):
    _select(page.needs.low_table, 0)
    page.needs.plan_selected_shortage()
    assert page._dest_input.currentData() == "001"
    shortage = page.needs.shortages[0]
    assert page.draft() == [("PLT-4410", shortage.suggested_qty)]
    assert page._draft_request_ids == []
