"""Offscreen GUI tests for admin_app's Distribution page."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships
from shared.models import Dealership, Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def data():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("A", "Oil", 10, 500, 5))
    on_time = ships.create("WH-01", "001", "Ridgeline", datetime.now() + timedelta(hours=3), [("A", 100)])
    ships.dispatch(on_time.id)
    late = ships.create("WH-02", "001", "Blue Ox", datetime.now() + timedelta(hours=1), [("A", 50)])
    ships.dispatch(late.id)
    ships.update_eta(late.id, datetime.now() + timedelta(hours=4))
    planned = ships.create("WH-01", "001", "Coastline", datetime.now() + timedelta(days=2), [("A", 7)])
    received = ships.create("WH-01", "001", "Ridgeline", datetime.now(), [("A", 24)])
    ships.dispatch(received.id)
    ships.complete_receipt(received.id, {"A": 20}, "crushed")
    return on_time, late, planned, received


@pytest.fixture
def page(qapp, data):
    from admin_app.gui.pages.distribution_page import DistributionPage

    widget = DistributionPage()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_kpis(page):
    assert page._transit_card._value_label.text() == "150"
    assert "2 shipments on the road · 2 origins" in page._transit_note.text()
    assert page._delayed_card._value_label.text() == "1"
    assert "Blue Ox" in page._delayed_note.text()
    assert page._today_card._value_label.text() == "1"


def test_route_tracker_lists_active_shipments(page, data):
    on_time, late, planned, received = data
    assert {r.shipment.id for r in page._tracker.rows()} == {on_time.id, late.id, planned.id}


def test_filter_and_selection_sync(page, data):
    on_time, late, planned, received = data
    page.set_filter("Delayed")
    assert [page._table.item(r, 0).text() for r in range(page._table.rowCount())] == [late.number]
    assert "(+3h" in page._table.item(0, 6).text()

    page.set_filter("All")
    page._toggle_select(on_time.id)
    assert page.selected_id() == on_time.id
    selected_rows = page._table.selectionModel().selectedRows()
    assert page._table.item(selected_rows[0].row(), 0).text() == on_time.number
    page._toggle_select(on_time.id)
    assert page.selected_id() is None


def test_delivered_section_shows_discrepancies(page, data):
    received = data[3]
    assert page._delivered_table.rowCount() == 1
    assert page._delivered_table.item(0, 0).text() == received.number
    assert page._delivered_table.item(0, 3).text() == "20"  # what arrived, not the 24 shipped
    assert page._delivered_table.item(0, 5).text() == "A -4 · “crushed”"
