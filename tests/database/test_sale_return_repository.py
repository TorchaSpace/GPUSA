"""Refunds: a manager approves, units go back on the shelf (or are written off),
the same units can't be refunded twice, and reports read the sale net."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import database.connection as connection
from database import account_repository as accounts
from database import audit_repository, dealership_repository, employee_repository, product_repository
from database import sale_return_repository as returns
from database import stock_repository
from database import transaction_repository as sales
from database.exceptions import ReturnApprovalError, ReturnQuantityError, SessionInvalidError, TransactionNotFoundError
from shared.auth import Actor
from shared.i18n import UserError
from shared.models import UNASSIGNED, Dealership, Employee, LineItem, Product, StockLocation, Transaction
from tests.stock_invariant import assert_totals_consistent

SHELF = StockLocation.dealership("001")
CASHIER = Actor("B-2", "Selin Kaya")
MANAGER = Actor("B-1", "Erol Admin")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def sale():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 10, 2))
    product_repository.create(Product("RICE", "Rice", 4.0, 10, 2))
    stock_repository.transfer(UNASSIGNED, SHELF, "OIL", 10)
    stock_repository.transfer(UNASSIGNED, SHELF, "RICE", 10)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-2", "cashier", "5831")
    return sales.finalize_transaction(
        Transaction(items=[LineItem("OIL", "Oil", 10.0, 3), LineItem("RICE", "Rice", 4.0, 2)], payment_method="cash"),
        SHELF, CASHIER)


def _shelf(barcode):
    return stock_repository.quantity_at(SHELF, barcode)


def test_a_refund_puts_goods_back_and_keeps_the_money_trail(sale):
    assert (_shelf("OIL"), _shelf("RICE")) == (7, 8)
    refund = returns.create(sale.id, [("OIL", 2, True)], "Wrong size", MANAGER, CASHIER)
    assert (refund.number, refund.total, refund.payment_method, refund.dealership_code) == ("RF-00001", 20.0, "cash", "001")
    assert (refund.requested_by, refund.approved_by) == ("Selin Kaya · B-2", "Erol Admin · B-1")
    assert _shelf("OIL") == 9
    assert product_repository.get_by_barcode("OIL").stock_quantity == 9
    assert_totals_consistent()
    [entry] = audit_repository.list_recent(kind="sale")
    assert entry.action == "refunded" and entry.target == f"#{sale.id}" and "RF-00001" in entry.detail


def test_a_damaged_unit_is_refunded_but_not_restocked(sale):
    returns.create(sale.id, [("RICE", 1, False)], "Torn bag", MANAGER)
    assert _shelf("RICE") == 8
    assert returns.returnable_lines(sale.id)[1].available == 1
    assert_totals_consistent()


def test_the_same_units_cannot_be_refunded_twice(sale):
    returns.create(sale.id, [("OIL", 3, True)], "All back", MANAGER)
    with pytest.raises(ReturnQuantityError) as err:
        returns.create(sale.id, [("OIL", 1, True)], "Again", MANAGER)
    assert err.value.available == 0
    with pytest.raises(ReturnQuantityError):
        returns.create(sale.id, [("RICE", 3, True)], "Too many", MANAGER)  # sold 2
    with pytest.raises(ReturnQuantityError):
        returns.create(sale.id, [("NOPE", 1, True)], "Not on the sale", MANAGER)
    assert len(returns.list_for_transaction(sale.id)) == 1


def test_a_refund_needs_a_manager_and_a_reason_and_whole_quantities(sale):
    with pytest.raises(ReturnApprovalError):
        returns.create(sale.id, [("OIL", 1, True)], "x", CASHIER)  # a cashier can't approve
    with pytest.raises(ReturnApprovalError):
        returns.create(sale.id, [("OIL", 1, True)], "x", None)
    with pytest.raises(UserError):
        returns.create(sale.id, [("OIL", 1, True)], "  ", MANAGER)
    with pytest.raises(UserError):
        returns.create(sale.id, [], "x", MANAGER)
    for bad in (0, -1, 1.5):
        with pytest.raises(UserError):
            returns.create(sale.id, [("OIL", bad, True)], "x", MANAGER)
    with pytest.raises(TransactionNotFoundError):
        returns.create(999, [("OIL", 1, True)], "x", MANAGER)
    assert returns.list_for_transaction(sale.id) == [] and _shelf("OIL") == 7


def test_the_requesting_cashier_must_still_be_active(sale):
    with pytest.raises(SessionInvalidError):
        returns.create(sale.id, [("OIL", 1, True)], "x", MANAGER, Actor("B-9", "Nobody"))


def test_the_same_key_refunds_once(sale):
    first = returns.create(sale.id, [("OIL", 1, True)], "Dup", MANAGER, client_uuid="rf-key")
    again = returns.create(sale.id, [("OIL", 1, True)], "Dup", MANAGER, client_uuid="rf-key")
    assert again.id == first.id and _shelf("OIL") == 8


def test_reports_read_the_sale_net_of_refunds(sale):
    window = (datetime.now() - timedelta(days=1), datetime.now() + timedelta(days=1))
    returns.create(sale.id, [("OIL", 1, True), ("RICE", 1, False)], "Part back", MANAGER)
    [net] = sales.list_between(*window)
    assert {i.product_barcode: i.quantity for i in net.items} == {"OIL": 2, "RICE": 1}
    assert net.total == 24.0
    [gross] = sales.list_between(*window, net_of_returns=False)
    assert gross.total == 38.0
    returns.create(sale.id, [("OIL", 2, True), ("RICE", 1, True)], "Rest", MANAGER)
    assert sales.list_between(*window) == []  # nothing left of that sale
    assert [r.number for r in returns.list_between(*window)] == ["RF-00001", "RF-00002"]
    assert sales.get_by_id(sale.id).total == 38.0  # the receipt itself is untouched
