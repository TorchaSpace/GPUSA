"""Product delete safety, validation, deactivation, initial-stock audit,
case-insensitive barcodes, the per-location / network-wide low-stock rule
and the checkout re-check of the cart."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import (
    dealership_repository,
    product_repository,
    shipment_repository as ships,
    stock_repository as stock,
    transaction_repository,
    warehouse_repository,
)
from database.exceptions import (
    DataAccessError,
    DuplicateBarcodeError,
    ProductInactiveError,
    ProductInUseError,
    ProductNotFoundError,
)
from shared.models import UNASSIGNED, Dealership, LineItem, Product, StockLocation, Transaction, Warehouse
from tests.stock_invariant import assert_totals_consistent

SHOP = StockLocation.dealership("001")
WH = StockLocation.warehouse("WH-01")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _p(barcode="SKU-1", **overrides) -> Product:
    fields = dict(barcode=barcode, name="Widget", price=5.0, stock_quantity=0, critical_stock_level=3)
    fields.update(overrides)
    return Product(**fields)


# --- delete -------------------------------------------------------------------

def test_delete_with_stock_is_refused_and_nothing_is_lost():
    product_repository.create(_p(stock_quantity=7))
    with pytest.raises(ProductInUseError, match="Deactivate"):
        product_repository.delete("SKU-1")
    assert product_repository.get_by_barcode("SKU-1").stock_quantity == 7
    assert stock.quantity_at(UNASSIGNED, "SKU-1") == 7
    assert_totals_consistent()


def test_delete_with_history_is_a_clear_error_not_a_raw_integrity_error():
    product_repository.create(_p(stock_quantity=5))
    stock.dispatch(UNASSIGNED, "SKU-1", 5, "scrapped")  # stock is 0 now, history remains
    with pytest.raises(DataAccessError, match="stock movements"):
        product_repository.delete("SKU-1")
    assert product_repository.get_by_barcode("SKU-1")


def test_delete_with_sales_or_shipments_is_refused():
    dealership_repository.create(Dealership(code="001", name="H", region="Coastal", city="N"))
    product_repository.create(_p("SOLD"))
    product_repository.create(_p("SHIPPED"))
    stock.receive(SHOP, "SOLD", 2)
    transaction_repository.finalize_transaction(
        Transaction(items=[LineItem("SOLD", "Widget", 5.0, 2)]), SHOP)
    ships.create("WH", "001", "X", ships_eta(), [("SHIPPED", 1)])
    with pytest.raises(ProductInUseError, match="movements|past sales"):
        product_repository.delete("SOLD")
    with pytest.raises(ProductInUseError, match="shipments"):
        product_repository.delete("SHIPPED")


def ships_eta():
    from datetime import datetime, timedelta

    return datetime.now() + timedelta(days=1)


def test_delete_of_an_untouched_product_still_works():
    product_repository.create(_p())
    product_repository.delete("sku-1")  # any letter case
    with pytest.raises(ProductNotFoundError):
        product_repository.get_by_barcode("SKU-1")


# --- deactivate / reactivate ---------------------------------------------------

def test_deactivated_products_are_hidden_from_sale_lists_but_kept_in_admin():
    product_repository.create(_p("A", stock_quantity=4))
    product_repository.create(_p("B"))
    product_repository.set_active("B", False)

    assert [p.barcode for p in product_repository.list_all()] == ["A", "B"]
    assert [p.is_active for p in product_repository.list_all()] == [True, False]
    assert [p.barcode for p in product_repository.list_active()] == ["A"]
    assert [p.barcode for p in stock.products_at(UNASSIGNED)] == ["A"]
    assert [p.barcode for p in stock.products_at(UNASSIGNED, include_inactive=True)] == ["A", "B"]
    with pytest.raises(ProductInactiveError):
        product_repository.get_by_barcode("B", active_only=True)
    with pytest.raises(ProductInactiveError):
        stock.product_at(UNASSIGNED, "B", active_only=True)
    assert product_repository.get_by_barcode("B").is_active is False  # admin can still read it

    product_repository.set_active("B", True)
    assert [p.barcode for p in stock.products_at(UNASSIGNED)] == ["A", "B"]


def test_a_deactivated_product_that_still_has_stock_stays_visible_where_the_stock_is():
    product_repository.create(_p("A", stock_quantity=4))
    product_repository.set_active("A", False)
    [p] = stock.products_at(UNASSIGNED)
    assert (p.barcode, p.stock_quantity, p.is_active) == ("A", 4, False)


def test_inactive_products_cannot_be_received_or_shipped_but_can_be_written_off():
    dealership_repository.create(Dealership(code="001", name="H", region="Coastal", city="N"))
    product_repository.create(_p("A", stock_quantity=4))
    product_repository.set_active("A", False)
    with pytest.raises(ProductInactiveError):
        stock.receive(UNASSIGNED, "A", 1)
    with pytest.raises(ProductInactiveError):
        ships.create("X", "001", "C", ships_eta(), [("A", 1)])
    assert stock.dispatch(UNASSIGNED, "A", 4) == 0  # clearing the shelf is still allowed
    assert_totals_consistent()


def test_set_active_on_a_missing_product():
    with pytest.raises(ProductNotFoundError):
        product_repository.set_active("NOPE", False)


def test_update_leaves_the_active_flag_alone():
    product_repository.create(_p())
    product_repository.set_active("SKU-1", False)
    product_repository.update(_p(name="Renamed"))
    assert product_repository.get_by_barcode("SKU-1").is_active is False


def test_inactive_products_are_not_critical_anywhere():
    product_repository.create(_p("A", stock_quantity=1, critical_stock_level=5))
    assert [p.barcode for p in product_repository.get_critical_stock_list()] == ["A"]
    product_repository.set_active("A", False)
    assert product_repository.get_critical_stock_list() == []
    assert stock.critical_at(UNASSIGNED) == []


# --- create / update validation ------------------------------------------------

@pytest.mark.parametrize(
    "overrides",
    [
        dict(name=""), dict(name="   "), dict(barcode=""), dict(barcode="  "),
        dict(price=float("nan")), dict(price=float("inf")), dict(price=-0.01), dict(price="abc"), dict(price=None),
        dict(critical_stock_level=-1), dict(critical_stock_level=2.5), dict(critical_stock_level="x"),
        dict(stock_quantity=-1), dict(stock_quantity=1.5),
    ],
)
def test_create_rejects_bad_fields_with_value_error(overrides):
    with pytest.raises(ValueError):
        product_repository.create(_p(**overrides))
    assert product_repository.list_all() == []


@pytest.mark.parametrize("overrides", [dict(name=" "), dict(price=float("nan")), dict(price=-1), dict(critical_stock_level=-2)])
def test_update_validates_too(overrides):
    product_repository.create(_p())
    with pytest.raises(ValueError):
        product_repository.update(_p(**overrides))
    assert product_repository.get_by_barcode("SKU-1").name == "Widget"


def test_values_are_cleaned_and_price_is_quantised_to_two_decimals():
    product_repository.create(_p("  sku-9 ", name="  Padded name ", price=19.999))
    p = product_repository.get_by_barcode("SKU-9")
    assert (p.barcode, p.name, p.price) == ("SKU-9", "Padded name", 20.0)
    product_repository.update(_p("SKU-9", name="N", price=0.125))
    assert product_repository.get_by_barcode("SKU-9").price == 0.13


def test_a_zero_price_is_allowed_in_the_repository_but_not_negative():
    product_repository.create(_p(price=0))
    assert product_repository.get_by_barcode("SKU-1").price == 0


def test_duplicate_barcode_is_case_insensitive_and_only_reported_when_real():
    product_repository.create(_p("abc-1"))
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(_p("ABC-1"))
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(_p(" Abc-1 "))
    assert [p.barcode for p in product_repository.list_all()] == ["ABC-1"]


def test_a_constraint_failure_is_not_reported_as_a_duplicate(monkeypatch):
    """If the database rejects an insert for another reason, the barcode
    doesn't exist, so DuplicateBarcodeError would be a lie."""
    with connection.connection_scope() as conn:  # make the schema exist
        conn.execute("CREATE TRIGGER nope BEFORE INSERT ON products BEGIN SELECT RAISE(ABORT, 'CHECK constraint failed: nope'); END")
    import sqlite3

    with pytest.raises((ValueError, sqlite3.IntegrityError)) as info:
        product_repository.create(_p("FRESH"))
    assert not isinstance(info.value, DuplicateBarcodeError)


def test_lookups_find_the_product_whatever_the_letter_case():
    product_repository.create(_p("ABC-1", stock_quantity=3))
    assert product_repository.get_by_barcode("abc-1").barcode == "ABC-1"
    assert stock.product_at(UNASSIGNED, "abc-1").stock_quantity == 3
    assert stock.receive(UNASSIGNED, "abc-1", 2) == 5
    assert stock.quantity_at(UNASSIGNED, "ABC-1") == 5
    assert_totals_consistent()


# --- initial stock audit --------------------------------------------------------

def test_initial_stock_writes_an_audit_movement():
    product_repository.create(_p(stock_quantity=12))
    [m] = stock.list_movements()
    assert (m["barcode"], m["movement_type"], m["quantity"], m["location"], m["reason"]) == (
        "SKU-1", "receive", 12, UNASSIGNED, "receive")
    assert "Initial stock" in m["note"]
    assert_totals_consistent()


def test_no_initial_stock_means_no_movement():
    product_repository.create(_p(stock_quantity=0))
    assert stock.list_movements() == []


# --- low-stock rule --------------------------------------------------------------

def test_a_new_empty_product_with_no_reorder_level_is_not_critical():
    product_repository.create(_p("NEW", stock_quantity=0, critical_stock_level=0))
    assert product_repository.get_critical_stock_list() == []
    assert not Product("X", "x", 1, 0, 0).is_below_critical_stock
    assert Product("X", "x", 1, 3, 3).is_below_critical_stock
    assert not Product("X", "x", 1, 4, 3).is_below_critical_stock


def test_network_total_decides_the_network_wide_list():
    product_repository.create(_p("A", stock_quantity=2, critical_stock_level=5))
    product_repository.create(_p("B", stock_quantity=9, critical_stock_level=5))
    assert [p.barcode for p in product_repository.get_critical_stock_list()] == ["A"]


def test_a_location_is_only_alerted_about_products_it_has_a_level_for():
    dealership_repository.create(Dealership(code="001", name="H", region="Coastal", city="N"))
    product_repository.create(_p("A", stock_quantity=10, critical_stock_level=5))
    product_repository.create(_p("B", stock_quantity=10, critical_stock_level=5))
    stock.transfer(UNASSIGNED, SHOP, "A", 4)  # shop has A (below 5), never had B
    assert [p.barcode for p in stock.critical_at(SHOP)] == ["A"]

    stock.transfer(SHOP, UNASSIGNED, "A", 4)  # sold/moved out entirely: it HAS stocked A, so still flagged
    assert [p.barcode for p in stock.critical_at(SHOP)] == ["A"]
    assert {p.barcode: p.stocked_here for p in stock.products_at(SHOP)} == {"A": True, "B": False}


# --- cart staleness ---------------------------------------------------------------

def _shop_with_widget(price=5.0, qty=10):
    dealership_repository.create(Dealership(code="001", name="H", region="Coastal", city="N"))
    product_repository.create(_p("W", price=price, stock_quantity=qty))
    stock.transfer(UNASSIGNED, SHOP, "W", qty)


def test_check_cart_is_clean_when_nothing_changed():
    _shop_with_widget()
    assert product_repository.check_cart(SHOP, [("W", 5.0, 3)]) == []
    assert product_repository.check_cart(SHOP, [("w", 5.0, 3)]) == []  # scanner case


def test_check_cart_reports_a_changed_price():
    _shop_with_widget()
    product_repository.update(_p("W", price=6.5))
    [issue] = product_repository.check_cart(SHOP, [("W", 5.0, 3)])
    assert (issue.kind, issue.cart_price, issue.current_price) == ("price", 5.0, 6.5)
    assert "$5.00" in issue.message and "$6.50" in issue.message


def test_check_cart_reports_deactivated_deleted_and_short_lines():
    _shop_with_widget(qty=4)
    product_repository.create(_p("GONE"))
    product_repository.create(_p("OFF", stock_quantity=0))
    product_repository.set_active("OFF", False)
    product_repository.delete("GONE")
    issues = product_repository.check_cart(SHOP, [("W", 5.0, 5), ("OFF", 5.0, 1), ("GONE", 5.0, 1), ("NEVER", 1.0, 1)])
    assert [(i.barcode, i.kind, i.available) for i in issues] == [
        ("W", "stock", 4), ("OFF", "unavailable", 0), ("GONE", "unavailable", 0), ("NEVER", "unavailable", 0)]


def test_cart_issues_pure_function_sums_duplicate_lines():
    here = Product("W", "Widget", 5.0, 4)
    [issue] = product_repository.cart_issues([("W", 5.0, 3), ("W", 5.0, 2)], {"W": here})
    assert issue.kind == "stock"
    assert product_repository.cart_issues([("W", 5.0, 2)], {"W": here}) == []
