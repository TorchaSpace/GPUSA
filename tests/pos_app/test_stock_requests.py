"""Offscreen tests: asking the depot for stock from My Local Stock."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import dealership_repository, product_repository, stock_repository, stock_request_repository
from shared.models import UNASSIGNED, Dealership, Product, StockLocation

SHELF = StockLocation.dealership("001")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def page(qapp):
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 50, 10))
    product_repository.create(Product("RICE", "Rice", 4.0, 50, 2))
    product_repository.create(Product("TEA", "Tea", 3.0, 50, 2))  # never stocked here
    stock_repository.transfer(UNASSIGNED, SHELF, "OIL", 3)
    stock_repository.transfer(UNASSIGNED, SHELF, "RICE", 20)
    from pos_app.gui.pages.my_stock_page import MyStockPage

    widget = MyStockPage(SHELF)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_the_dialog_puts_the_low_product_first_and_suggests_a_refill(page):
    dialog = page.request_dialog()
    assert dialog.product_combo.currentData() == "OIL"
    assert dialog.quantity_spin.value() == 17  # back up to 2 x 10
    assert [dialog.product_combo.itemData(i) for i in range(dialog.product_combo.count())] == ["OIL", "RICE", "TEA"]


def test_sending_a_request_records_it_and_the_row_shows_it(page):
    dialog = page.request_dialog("OIL")
    dialog.quantity_spin.setValue(12)
    dialog.note_input.setText("weekend")
    assert dialog.submit()
    [request] = stock_request_repository.list_open()
    assert (request.dealership_code, request.product_barcode, request.quantity, request.note) == ("001", "OIL", 12, "weekend")
    page.reload(play=False)
    assert "My requests (1)" in page.my_requests_button.text()
    assert page._requested == {"OIL": 12}


def test_asking_twice_shows_the_error_in_the_dialog(page):
    stock_request_repository.create("001", "OIL", 5)
    dialog = page.request_dialog("OIL")
    assert not dialog.submit()
    assert dialog.error_label.isVisibleTo(dialog) and "already an open request" in dialog.error_label.text()


def test_my_requests_lists_them_and_withdraws_a_waiting_one(page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from pos_app.gui.stock_requests import MyRequestsDialog, request_state_text

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))

    waiting = stock_request_repository.create("001", "OIL", 5)
    declined = stock_request_repository.create("001", "RICE", 5)
    stock_request_repository.decline(declined.id, "Out until Monday")
    dialog = MyRequestsDialog("001")
    assert {r.id for r in dialog.requests} == {waiting.id, declined.id}
    assert request_state_text(stock_request_repository.get(declined.id)) == "Declined: Out until Monday"
    assert dialog.withdraw(waiting.id)
    assert stock_request_repository.get(waiting.id).status == "cancelled"
    assert not dialog.withdraw(waiting.id)  # already withdrawn
    assert warnings and "withdrawn" in warnings[0]
    dialog.close()
    dialog.deleteLater()


def test_a_till_without_a_dealership_has_no_request_buttons(qapp):
    from pos_app.gui.pages.my_stock_page import MyStockPage

    widget = MyStockPage(UNASSIGNED)
    assert not widget.request_button.isVisibleTo(widget)
    assert not widget.my_requests_button.isVisibleTo(widget)
