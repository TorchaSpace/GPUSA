"""Offscreen GUI tests for admin_app's Overview page."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, stock_repository, transaction_repository, warehouse_repository
from database.connection import connection_scope
from shared.models import UNASSIGNED, Dealership, LineItem, Product, Transaction, Warehouse

TODAY = date(2026, 9, 24)
WH1 = Warehouse("WH-01", "Duluth", capacity_units=100)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _sale(day_iso: str, amount: float, dealership_code: str | None = None) -> None:
    saved = transaction_repository.finalize_transaction(
        Transaction(items=[LineItem(product_barcode="BOX", product_name_at_sale="Carton", unit_price_at_sale=amount, quantity=1)])
    )
    with connection_scope() as conn:
        conn.execute(
            "UPDATE transactions SET created_at = ?, dealership_code = ? WHERE id = ?",
            (f"{day_iso}T12:00:00.000Z", dealership_code, saved.id),
        )
        conn.commit()


@pytest.fixture
def seeded():
    warehouse_repository.create(WH1)
    dealership_repository.create(Dealership("CST-04", "Harbor Point", "Coastal", "Norfolk"))
    dealership_repository.create(Dealership("MET-01", "Metro Heavy", "Metro", "Columbus"))
    product_repository.create(Product("BOX", "Carton", 1.0, stock_quantity=50, critical_stock_level=5))
    product_repository.create(Product("TAPE", "Tape", 1.0, stock_quantity=2, critical_stock_level=5))
    stock_repository.transfer(UNASSIGNED, WH1.location, "BOX", 30)  # 30 in the warehouse, 20 unassigned
    _sale("2026-09-24", 40, "CST-04")
    _sale("2026-08-10", 20, "CST-04")


@pytest.fixture
def page(qapp, seeded):
    from admin_app.gui.pages.overview_page import OverviewPage

    widget = OverviewPage(today_provider=lambda: TODAY)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _col(page, header):
    table = page._stock_table
    names = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    return [table.item(r, names.index(header)).text() for r in range(table.rowCount())]


def test_revenue_card_has_this_months_sales_and_the_change(page):
    assert page._revenue_card._value_label.text() == "40.00"
    assert page._revenue_card._trend_label.text() == "▲ 100.0%"  # August's comparable stretch was 20
    assert page._revenue_card._corner_label.text() == "vs Aug"


def test_warehouse_card_shows_real_capacity_use(page):
    assert page._warehouse_card._value_label.text() == "30%"  # 30 of 100 units
    assert page._warehouse_card._corner_label.text() == "1 active"


def test_dealership_card_counts_active_and_silent_dealerships(page):
    assert page._dealership_card._value_label.text() == "2"
    assert "1 with no sales this month" in page._dealership_card._trend_label.text()


def test_stock_grid_columns_rows_and_statuses(page):
    table = page._stock_table
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert headers == ["SKU", "Product", "WH-01", "Dealerships", "Unassigned", "Total", "Reorder at", "Status"]
    assert _col(page, "SKU") == ["BOX", "TAPE"]
    assert _col(page, "WH-01") == ["30", "0"] and _col(page, "Unassigned") == ["18", "2"]
    assert _col(page, "Total") == ["48", "2"]
    assert _col(page, "Status") == ["In stock", "Low stock"]


def test_filters_narrow_the_grid(page):
    page.set_low_only(True)
    assert _col(page, "SKU") == ["TAPE"]
    page.set_low_only(False)
    page.set_location_filter("warehouses")
    assert _col(page, "Total") == ["30", "0"]
    assert "Unassigned" not in [page._stock_table.horizontalHeaderItem(c).text() for c in range(page._stock_table.columnCount())]
    page.set_location_filter("all")
    page.set_query("tap")
    assert _col(page, "SKU") == ["TAPE"]


def test_pending_approvals_button_shows_the_count(page):
    page.set_pending_count(4)
    assert page._approvals_button.text().endswith("4")
    page.set_pending_count(0)
    assert page._approvals_button.text() == "Pending approvals"


def test_page_opens_on_an_empty_database(qapp):
    from admin_app.gui.pages.overview_page import OverviewPage

    widget = OverviewPage(today_provider=lambda: TODAY)
    pump(qapp)
    assert widget._revenue_card._value_label.text() == "0.00"
    assert widget._warehouse_card._value_label.text() == "—"
    assert widget._stock_table.rowCount() == 0
    widget.close()
