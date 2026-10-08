"""Offscreen tests: the till's Sales page - refunds approved by a manager, and the day-close count."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import account_repository as accounts
from database import day_close_repository, dealership_repository, employee_repository, product_repository
from database import sale_return_repository, stock_repository
from database import transaction_repository as sales
from shared import current_session
from shared.auth import Actor
from shared.models import UNASSIGNED, Dealership, Employee, LineItem, Product, StockLocation, Transaction

SHELF = StockLocation.dealership("001")
CASHIER = Actor("B-2", "Selin Kaya")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    yield
    current_session.clear()


@pytest.fixture
def page(qapp):
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 50, 2))
    stock_repository.transfer(UNASSIGNED, SHELF, "OIL", 10)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-2", "cashier", "5831")
    session = accounts.authenticate("B-2", "5831", "pos", "Till 1")
    current_session.set(session)
    sales.finalize_transaction(
        Transaction(items=[LineItem("OIL", "Oil", 10.0, 3)], payment_method="cash"), SHELF, CASHIER)
    from pos_app.gui.sales_page import SalesPage

    widget = SalesPage(SHELF)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _fill(dialog, qty=2, badge="B-1", pin="482913", reason="Wrong size"):
    dialog.quantity_spins["OIL"].setValue(qty)
    dialog.reason_input.setText(reason)
    dialog.badge_input.setText(badge)
    dialog.pin_input.setText(pin)


def test_the_page_lists_this_tills_recent_sales(page):
    assert page.table.rowCount() == 1
    assert page.table.item(0, 5).text().endswith("30.00") and not page.return_button.isEnabled()
    page.table.selectRow(0)
    assert page.return_button.isEnabled()


def test_a_manager_signs_off_a_refund_and_the_shelf_gets_the_goods_back(page):
    page.table.selectRow(0)
    dialog = page.return_dialog()
    _fill(dialog)
    assert dialog.refund_total() == 20.0
    assert dialog.submit()
    assert dialog.sale_return.approved_by == "Erol Admin · B-1" and dialog.sale_return.requested_by == "Selin Kaya · B-2"
    assert stock_repository.quantity_at(SHELF, "OIL") == 9
    page.reload()
    assert page.table.item(0, 6).text().endswith("20.00")


def test_a_cashier_cannot_approve_their_own_refund(page):
    page.table.selectRow(0)
    dialog = page.return_dialog()
    _fill(dialog, badge="B-2", pin="5831")
    assert not dialog.submit()
    assert dialog.error_label.isVisibleTo(dialog) and dialog.error_label.text()
    assert dialog.pin_input.text() == ""
    assert sale_return_repository.list_for_transaction(page.transactions[0].id) == []


def test_a_wrong_pin_or_too_many_units_is_shown_not_raised(page):
    page.table.selectRow(0)
    dialog = page.return_dialog()
    _fill(dialog, pin="000000")
    assert not dialog.submit() and dialog.error_label.text()
    dialog = page.return_dialog()
    dialog.quantity_spins["OIL"].setMaximum(99)
    _fill(dialog, qty=5)
    assert not dialog.submit() and "3" in dialog.error_label.text()


def test_closing_the_day_stores_the_count_against_the_sales(page):
    from pos_app.gui.sales_page import DayCloseDialog

    dialog = DayCloseDialog("001")
    assert dialog.summary.expected_cash == 30.0 and dialog.counted_spin.value() == 30.0
    dialog.counted_spin.setValue(28.0)
    assert "2.00" in dialog.difference_label.text() and "short" in dialog.difference_label.text()
    dialog.note_input.setText("coin jar")
    assert dialog.submit()
    [saved] = day_close_repository.list_recent()
    assert (saved.difference, saved.note, saved.closed_by) == (-2.0, "coin jar", "Selin Kaya · B-2")
    dialog.close()
    dialog.deleteLater()
