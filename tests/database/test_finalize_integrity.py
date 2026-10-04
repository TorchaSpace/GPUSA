"""finalize_transaction is the authority on a sale: it re-checks prices,
products, the cashier's account, the dealership and stock inside its own
transaction, and a refused sale writes nothing."""

from __future__ import annotations

import threading

import pytest

import database.connection as connection
from database import account_repository as accounts
from database import dealership_repository, employee_repository, product_repository, stock_repository
from database import transaction_repository as sales
from database.exceptions import (
    DealershipInactiveError,
    InsufficientStockError,
    PriceChangedError,
    ProductInactiveError,
    ProductNotFoundError,
    SessionInvalidError,
)
from shared.auth import Actor
from shared.models import UNASSIGNED, Dealership, Employee, LineItem, Product, StockLocation, Transaction

SHELF = StockLocation.dealership("001")
CASHIER = Actor("B-2", "Selin Kaya")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def shop():
    """A dealership with OIL (10.00) and RICE (4.00), 10 of each on its shelf,
    and a cashier with a live POS account."""
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 10, 2))
    product_repository.create(Product("RICE", "Rice", 4.0, 10, 2))
    stock_repository.transfer(UNASSIGNED, SHELF, "OIL", 10)
    stock_repository.transfer(UNASSIGNED, SHELF, "RICE", 10)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    accounts.create_first_admin("B-1", "Erol Admin", "482913")
    accounts.create_account("B-2", "cashier", "5831")


def _sale(*lines) -> Transaction:
    return Transaction(items=[LineItem(*line) for line in lines])


def _oil(qty=1, price=10.0):
    return ("OIL", "Oil", price, qty)


def _rice(qty=1, price=4.0):
    return ("RICE", "Rice", price, qty)


def _written():
    with connection.connection_scope() as conn:
        return (
            conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM transaction_items").fetchone()[0],
        )


def _shelf():
    return stock_repository.quantity_at(SHELF, "OIL"), stock_repository.quantity_at(SHELF, "RICE")


def _total(barcode):
    return product_repository.get_by_barcode(barcode).stock_quantity


def _nothing_written():
    assert _written() == (0, 0)
    assert _shelf() == (10, 10)
    assert (_total("OIL"), _total("RICE")) == (10, 10)


# --- price ------------------------------------------------------------------


def test_a_sale_at_current_prices_goes_through(shop):
    done = sales.finalize_transaction(_sale(_oil(2), _rice(1)), SHELF, CASHIER)
    assert done.total == 24.0 and _written() == (1, 2) and _shelf() == (8, 9)


def test_price_changed_between_cart_and_finalize_is_refused_with_the_old_and_new_price(shop):
    sale = _sale(_oil(2))  # the cart was built at 10.00
    product_repository.update(Product("OIL", "Oil", 12.5, 0, 2))  # an admin re-prices it

    with pytest.raises(PriceChangedError) as info:
        sales.finalize_transaction(sale, SHELF, CASHIER)

    assert info.value.changes == [("OIL", 10.0, 12.5)]
    assert (info.value.barcode, info.value.old_price, info.value.new_price) == ("OIL", 10.0, 12.5)
    assert "OIL" in str(info.value) and "10.00" in str(info.value) and "12.50" in str(info.value)
    _nothing_written()


def test_every_moved_line_is_reported_not_just_the_first(shop):
    sale = _sale(_oil(1), _rice(1))
    product_repository.update(Product("OIL", "Oil", 11.0, 0, 2))
    product_repository.update(Product("RICE", "Rice", 3.5, 0, 2))
    with pytest.raises(PriceChangedError) as info:
        sales.finalize_transaction(sale, SHELF, CASHIER)
    assert info.value.changes == [("OIL", 10.0, 11.0), ("RICE", 4.0, 3.5)]
    _nothing_written()


def test_prices_are_compared_in_cents_not_floats(shop):
    done = sales.finalize_transaction(_sale(_oil(1, price=10.004)), SHELF, CASHIER)  # same price to the cent
    assert done.id is not None
    with pytest.raises(PriceChangedError):
        sales.finalize_transaction(_sale(_oil(1, price=10.01)), SHELF, CASHIER)  # one cent off
    assert _written()[0] == 1


def test_one_moved_line_stops_the_whole_cart(shop):
    sale = _sale(_oil(1), _rice(1))
    product_repository.update(Product("RICE", "Rice", 5.0, 0, 2))
    with pytest.raises(PriceChangedError):
        sales.finalize_transaction(sale, SHELF, CASHIER)
    _nothing_written()  # the OIL line that was fine did not sneak through either


# --- product ----------------------------------------------------------------


def test_a_product_deactivated_mid_sale_is_refused(shop):
    sale = _sale(_oil(1), _rice(1))
    product_repository.set_active("RICE", False)
    with pytest.raises(ProductInactiveError) as info:
        sales.finalize_transaction(sale, SHELF, CASHIER)
    assert info.value.barcode == "RICE"
    _nothing_written()


def test_a_product_that_vanished_is_refused(shop):
    sale = _sale(("GHOST", "Ghost", 1.0, 1))
    with pytest.raises(ProductNotFoundError):
        sales.finalize_transaction(sale, SHELF, CASHIER)
    _nothing_written()


# --- stock ------------------------------------------------------------------


def test_stock_race_the_other_till_sold_it_first(shop):
    first = _sale(_oil(8))
    second = _sale(_oil(5))
    sales.finalize_transaction(first, SHELF, CASHIER)
    with pytest.raises(InsufficientStockError) as info:
        sales.finalize_transaction(second, SHELF, CASHIER)
    assert (info.value.requested, info.value.available) == (5, 2)
    assert _written() == (1, 1) and _shelf() == (2, 10)


def test_the_same_barcode_on_two_lines_is_summed_for_the_stock_check(shop):
    with pytest.raises(InsufficientStockError):
        sales.finalize_transaction(_sale(_oil(6), _oil(6)), SHELF, CASHIER)
    _nothing_written()


# --- cashier session ----------------------------------------------------------


def test_a_cashier_switched_off_mid_sale_is_refused(shop):
    sale = _sale(_oil(1))
    accounts.set_active("B-2", False, by=Actor("B-1", "Erol Admin"))
    with pytest.raises(SessionInvalidError):
        sales.finalize_transaction(sale, SHELF, CASHIER)
    _nothing_written()


def test_a_deactivated_employee_a_changed_role_or_unknown_badge_is_refused(shop):
    sale = _sale(_oil(1))
    with pytest.raises(SessionInvalidError):  # no such account at all
        sales.finalize_transaction(sale, SHELF, Actor("B-99", "Nobody"))
    accounts.set_role("B-2", "depot_manager", by=Actor("B-1", "Erol Admin"))  # may no longer use POS
    with pytest.raises(SessionInvalidError):
        sales.finalize_transaction(sale, SHELF, CASHIER)
    accounts.set_role("B-2", "cashier", by=Actor("B-1", "Erol Admin"))
    employee = employee_repository.get_by_badge_id("B-2")
    employee.is_active = False
    employee_repository.update(employee)
    with pytest.raises(SessionInvalidError):
        sales.finalize_transaction(sale, SHELF, CASHIER)
    _nothing_written()


def test_an_administrator_may_still_ring_up_sales_and_badges_match_in_any_case(shop):
    done = sales.finalize_transaction(_sale(_oil(1)), SHELF, Actor("b-1", "Erol Admin"))
    assert done.cashier == "Erol Admin · b-1"
    assert _written() == (1, 1)


def test_no_actor_means_no_session_check(shop):  # setup scripts and tests call it that way
    assert sales.finalize_transaction(_sale(_oil(1)), SHELF).id is not None


# --- dealership ---------------------------------------------------------------


def test_a_dealership_switched_off_mid_sale_is_refused(shop):
    sale = _sale(_oil(1))
    dealership_repository.update(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk",
                                            is_active=False))
    with pytest.raises(DealershipInactiveError) as info:
        sales.finalize_transaction(sale, SHELF, CASHIER)
    assert info.value.name == "Harbor Point"
    _nothing_written()


def test_switching_the_dealership_back_on_lets_sales_resume(shop):
    dealership_repository.update(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk",
                                            is_active=False))
    dealership_repository.update(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    assert sales.finalize_transaction(_sale(_oil(1)), SHELF, CASHIER).id is not None


# --- atomicity ----------------------------------------------------------------


def test_a_failure_while_writing_rolls_the_whole_sale_back(shop, monkeypatch):
    real = sales.change_level
    calls = []

    def flaky(conn, location, barcode, delta, **kwargs):
        calls.append(barcode)
        if len(calls) == 2:  # the second product's stock update dies after the first one succeeded
            raise RuntimeError("disk full")
        return real(conn, location, barcode, delta, **kwargs)

    monkeypatch.setattr(sales, "change_level", flaky)
    with pytest.raises(RuntimeError):
        sales.finalize_transaction(_sale(_oil(1), _rice(1)), SHELF, CASHIER)
    monkeypatch.setattr(sales, "change_level", real)

    assert calls == ["OIL", "RICE"]
    _nothing_written()  # header, items and the first deduction all rolled back


@pytest.mark.parametrize(
    "refuse",
    ["price", "inactive", "session", "dealership", "stock"],
)
def test_nothing_is_written_on_any_refusal(shop, refuse):
    sale = _sale(_oil(1), _rice(1))
    if refuse == "price":
        product_repository.update(Product("OIL", "Oil", 99.0, 0, 2))
    elif refuse == "inactive":
        product_repository.set_active("OIL", False)
    elif refuse == "session":
        accounts.set_active("B-2", False, by=Actor("B-1", "Erol Admin"))
    elif refuse == "dealership":
        dealership_repository.update(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk",
                                                is_active=False))
    else:
        sale = _sale(_oil(1), _rice(11))
    with pytest.raises(Exception) as info:
        sales.finalize_transaction(sale, SHELF, CASHIER)
    assert not isinstance(info.value, (AssertionError, TypeError, AttributeError))
    _nothing_written()
    with connection.connection_scope() as conn:  # nor any stock movement
        assert conn.execute("SELECT COUNT(*) FROM stock_movements WHERE reason = 'sale'").fetchone()[0] == 0


# --- concurrent sales -----------------------------------------------------------


def test_concurrent_sales_never_oversell(shop):
    outcomes, lock = [], threading.Lock()
    start = threading.Barrier(12)

    def sell():
        start.wait()
        try:
            sales.finalize_transaction(_sale(_oil(1)), SHELF, CASHIER)
            result = "sold"
        except InsufficientStockError:
            result = "short"
        except Exception as exc:  # anything else is a bug
            result = repr(exc)
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=sell) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(set(outcomes)) == ["short", "sold"], outcomes
    assert outcomes.count("sold") == 10 and outcomes.count("short") == 2
    assert _written() == (10, 10) and _shelf()[0] == 0 and _total("OIL") == 0


def test_a_price_change_racing_concurrent_sales_never_lets_a_stale_price_through(shop):
    """Sales built at 10.00 race an admin re-pricing OIL to 12.00: every sale
    that committed must be consistent with the price in force - none may
    record 10.00 after the change - and the rest must have been refused."""
    outcomes, lock = [], threading.Lock()
    start = threading.Barrier(9)

    def sell():
        start.wait()
        try:
            sales.finalize_transaction(_sale(_oil(1)), SHELF, CASHIER)
            result = "sold"
        except PriceChangedError:
            result = "refused"
        except Exception as exc:
            result = repr(exc)
        with lock:
            outcomes.append(result)

    def reprice():
        start.wait()
        product_repository.update(Product("OIL", "Oil", 12.0, 0, 2))

    threads = [threading.Thread(target=sell) for _ in range(8)] + [threading.Thread(target=reprice)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert set(outcomes) <= {"sold", "refused"}, outcomes
    with connection.connection_scope() as conn:
        recorded = [r[0] for r in conn.execute("SELECT unit_price_at_sale FROM transaction_items")]
    assert len(recorded) == outcomes.count("sold")
    assert set(recorded) <= {10.0}  # every sale that got through was at the price it was built with, before the change
    assert _shelf()[0] == 10 - len(recorded)
    # and once the dust settles a stale-priced sale is always refused
    with pytest.raises(PriceChangedError):
        sales.finalize_transaction(_sale(_oil(1)), SHELF, CASHIER)


# --- cost snapshot (migration v4 columns) -----------------------------------------


def _set_cost(barcode, cost):
    with connection.connection_scope() as conn:
        conn.execute("UPDATE products SET cost_price = ? WHERE barcode = ?", (cost, barcode))


def test_the_cost_in_force_is_snapshotted_with_the_sale(shop):
    _set_cost("OIL", 6.5)  # RICE has no cost on record
    done = sales.finalize_transaction(_sale(_oil(2), _rice(1)), SHELF, CASHIER)

    by_barcode = {i.product_barcode: i for i in done.items}
    assert (by_barcode["OIL"].unit_cost_at_sale, by_barcode["OIL"].cost_known) == (6.5, True)
    assert (by_barcode["RICE"].unit_cost_at_sale, by_barcode["RICE"].cost_known) == (0.0, False)
    with connection.connection_scope() as conn:
        rows = {r["product_barcode"]: (r["unit_cost_at_sale"], r["cost_known"])
                for r in conn.execute("SELECT * FROM transaction_items")}
    assert rows == {"OIL": (6.5, 1), "RICE": (0.0, 0)}

    _set_cost("OIL", 9.0)  # a later cost change never rewrites the past sale
    with connection.connection_scope() as conn:
        assert conn.execute("SELECT unit_cost_at_sale FROM transaction_items WHERE product_barcode = 'OIL'").fetchone()[0] == 6.5


def test_a_cart_cannot_smuggle_in_its_own_cost(shop):
    _set_cost("OIL", 6.5)
    line = LineItem("OIL", "Oil", 10.0, 1, unit_cost_at_sale=0.01, cost_known=True)
    done = sales.finalize_transaction(Transaction(items=[line]), SHELF, CASHIER)
    assert (done.items[0].unit_cost_at_sale, done.items[0].cost_known) == (6.5, True)
