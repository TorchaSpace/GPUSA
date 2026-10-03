"""Offscreen GUI tests for pos_app's Receive Inventory screen."""

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
def shipment():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    dealership_repository.create(Dealership(code="002", name="Riverbend", region="Valley", city="Boise"))
    product_repository.create(Product("A", "Oil", 10, 50, 5))
    product_repository.create(Product("B", "Rice", 10, 50, 5))
    s = ships.create("WH-01", "001", "Ridgeline", datetime.now() + timedelta(minutes=30), [("A", 24), ("B", 18)], "Murat")
    ships.dispatch(s.id)
    ships.create("WH-01", "002", "Other", datetime.now(), [("A", 1)])  # someone else's
    return s


def _page(qapp, code="001"):
    from pos_app.gui.pages.receive_page import ReceivePage

    page = ReceivePage(code, "Harbor Point")
    page.show()
    pump(qapp)
    return page


def test_lists_only_this_dealerships_shipments(qapp, shipment):
    page = _page(qapp)
    assert [s.id for s in page.shipments()] == [shipment.id]
    assert page.selected().id == shipment.id


def test_no_setup_file_lists_everything_and_says_so(qapp, shipment):
    page = _page(qapp, code=None)
    assert len(page.shipments()) == 2
    assert "isn't linked to a dealership" in page._scope_note.text()


def test_complete_needs_every_line_checked(qapp, shipment):
    page = _page(qapp)
    page.toggle_line("A")
    assert not page._complete_button.isEnabled()
    page.toggle_line("B")
    assert page._complete_button.isEnabled()
    assert page._complete_button.text() == "Complete receipt"


def test_accept_all_receives_in_full(qapp, shipment):
    page = _page(qapp)
    page._accept_all()
    page._complete()

    done = ships.get(shipment.id)
    assert done.status == "delivered" and done.discrepancies == []
    assert "received into stock" in page._message.text()
    assert product_repository.get_by_barcode("A").stock_quantity == 50


def test_report_discrepancy_sends_report_and_adjusts_stock(qapp, shipment):
    page = _page(qapp)
    changed = []
    page.stock_changed.connect(lambda: changed.append(True))
    page._toggle_report()
    page.change_received("B", -2)
    page.toggle_line("A")
    page._note_input.setText("2 bags torn")
    assert page._complete_button.text() == "Send report && receive"
    page._complete()

    done = ships.get(shipment.id)
    assert [(l.product_barcode, l.received_qty) for l in done.lines] == [("A", 24), ("B", 16)]
    assert done.receipt_note == "2 bags torn"
    assert product_repository.get_by_barcode("B").stock_quantity == 48
    assert changed == [True]
    assert page._complete_button.text() == "Receipt completed"  # still listed as received


def test_second_terminal_is_told_not_double_counted(qapp, shipment):
    page = _page(qapp)
    page._accept_all()
    ships.complete_receipt(shipment.id, {"B": 10})  # another till got there first
    page._complete()

    assert "can't be received" in page._message.text()
    assert product_repository.get_by_barcode("B").stock_quantity == 42  # adjusted once, by the other till


def test_home_badge_counts_incoming_for_this_dealership(qapp, shipment):
    from pos_app.gui.pages.home_page import HomePage

    home = HomePage("Cashier", dealership_code="001")
    assert home._receive_badge.text() == "1 arriving"


def test_plus_button_never_goes_above_the_shipped_quantity(qapp, shipment):
    page = _page(qapp)
    page._toggle_report()
    page.change_received("B", -3)
    assert page._received_for(page.selected(), "B") == 15
    for _ in range(10):  # tap + far more times than there are missing units
        page.change_received("B", 1)
    assert page._received_for(page.selected(), "B") == 18  # capped at what was shipped
    page.change_received("A", 5)
    assert page._received_for(page.selected(), "A") == 24
    assert page._complete_button.isEnabled()  # both lines were touched, so both are checked
    page._complete()
    assert product_repository.get_by_barcode("A").stock_quantity == 50  # no stock from nothing
    assert product_repository.get_by_barcode("B").stock_quantity == 50


def test_short_is_still_a_discrepancy_path(qapp, shipment):
    page = _page(qapp)
    page._toggle_report()
    for _ in range(100):
        page.change_received("A", -1)
    assert page._received_for(page.selected(), "A") == 0  # floor at zero
    page.toggle_line("B")
    page._complete()
    assert ships.get(shipment.id).lines[0].received_qty == 0


def test_a_shipment_that_has_not_left_the_depot_cannot_be_received(qapp, shipment):
    waiting = ships.create("WH-01", "001", "Later", datetime.now() + timedelta(hours=5), [("A", 5)])
    page = _page(qapp)
    page.select(waiting.id)

    assert not page._accept_button.isEnabled() and not page._report_button.isEnabled()
    assert not page._complete_button.isEnabled()
    assert page._complete_button.text() == "Not dispatched yet"
    page.toggle_line("A")  # ignored
    page.change_received("A", -1)  # ignored
    page._accept_all()  # ignored
    page._complete()  # ignored
    assert ships.get(waiting.id).status == "scheduled"
    assert product_repository.get_by_barcode("A").stock_quantity == 50
    assert page._complete_button.text() != "Complete receipt"


def test_a_repository_refusal_is_shown_not_raised(qapp, shipment):
    page = _page(qapp)
    page._accept_all()
    ships.cancel(shipment.id)  # the depot cancelled it while the cashier was counting
    page._complete()
    assert "Couldn't complete" in page._message.text()
