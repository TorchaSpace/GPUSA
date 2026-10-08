"""End-of-day count: sales by method, refunds on the day they were paid, expected cash vs counted."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

import database.connection as connection
from database import account_repository as accounts
from database import day_close_repository as closes
from database import dealership_repository, employee_repository, product_repository, stock_repository
from database import sale_return_repository as returns
from database import transaction_repository as sales
from shared.auth import Actor
from shared.i18n import UserError
from shared.models import UNASSIGNED, Dealership, Employee, LineItem, Product, StockLocation, Transaction

SHELF = StockLocation.dealership("001")
CASHIER = Actor("B-2", "Selin Kaya")
MANAGER = Actor("B-1", "Erol Admin")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def day():
    for code in ("001", "002"):
        dealership_repository.create(Dealership(code=code, name=f"Shop {code}", region="Metro", city="X"))
    product_repository.create(Product("OIL", "Oil", 10.0, 100, 2))
    for loc in (SHELF, StockLocation.dealership("002")):
        stock_repository.transfer(UNASSIGNED, loc, "OIL", 30)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Shop 001"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-2", "cashier", "5831")


def _sell(quantity, method, loc=SHELF):
    return sales.finalize_transaction(
        Transaction(items=[LineItem("OIL", "Oil", 10.0, quantity)], payment_method=method), loc, CASHIER)


def test_the_summary_splits_methods_and_nets_cash_refunds(day):
    cash = _sell(3, "cash")
    _sell(2, "card")
    _sell(1, None)
    _sell(5, "cash", StockLocation.dealership("002"))  # another shop: not counted
    returns.create(cash.id, [("OIL", 1, True)], "Changed mind", MANAGER)
    s = closes.summarize("001")
    assert (s.sales_count, s.cash_sales, s.card_sales, s.other_sales) == (3, 30.0, 20.0, 10.0)
    assert (s.cash_refunds, s.card_refunds, s.refunds_count) == (10.0, 0.0, 1)
    assert s.expected_cash == 20.0 and s.net_total == 50.0


def test_closing_stores_the_difference_and_can_be_repeated(day):
    _sell(3, "cash")
    first = closes.close("001", 28.5, actor=CASHIER, note="short a coin")
    assert (first.expected_cash, first.counted_cash, first.difference) == (30.0, 28.5, -1.5)
    assert first.closed_by == "Selin Kaya · B-2" and first.note == "short a coin"
    second = closes.close("001", 30)
    assert second.difference == 0 and closes.latest_for("001", date.today()).id == second.id
    assert [c.id for c in closes.list_recent(dealership_code="001")] == [second.id, first.id]
    assert [c.id for c in closes.list_recent(only_differences=True)] == [first.id]
    assert closes.latest_for("001", date.today() - timedelta(days=1)) is None


@pytest.mark.parametrize("counted", ["x", -1, None, float("nan")])
def test_a_bad_count_is_refused(day, counted):
    with pytest.raises(UserError):
        closes.close("001", counted)
    assert closes.list_recent() == []
