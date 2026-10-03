"""Offscreen GUI tests for pos_app's New Sale screen: the cart is re-read
from the database before charging, and refreshed when the page reloads."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, stock_repository, transaction_repository
from shared.models import UNASSIGNED, Dealership, Product, StockLocation

SHELF = StockLocation.dealership("001")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    seen = {"warning": [], "info": []}
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen["warning"].append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: seen["info"].append(a[2]))
    return seen


@pytest.fixture
def page(qapp, boxes, monkeypatch):
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("OIL", "Oil", 10.0, 20, 2))
    product_repository.create(Product("RICE", "Rice", 4.0, 20, 2))
    product_repository.create(Product("OFF", "Retired", 1.0, 0, 0))
    product_repository.set_active("OFF", False)
    stock_repository.transfer(UNASSIGNED, SHELF, "OIL", 10)
    stock_repository.transfer(UNASSIGNED, SHELF, "RICE", 10)
    import pos_app.gui.pages.new_sale_page as module

    # No receipt printer in a test: finalize through the repository directly.
    monkeypatch.setattr(module, "complete_sale",
                        lambda pending, location, cashier=None: transaction_repository.finalize_transaction(
                            pending, location, cashier))
    widget = module.NewSalePage(SHELF)
    widget.reload()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _tile_for(page, barcode):
    return next(p for p in page._all_products if p.barcode == barcode)


def test_price_change_between_adding_and_charging_is_refused_and_the_cart_refreshed(page, boxes):
    page._add_to_cart(_tile_for(page, "OIL"))
    page._add_to_cart(_tile_for(page, "OIL"))
    product_repository.update(Product("OIL", "Oil", 12.5, 0, 2))  # an admin re-prices it meanwhile

    page._checkout("Card")

    assert boxes["warning"] and "$10.00" in boxes["warning"][0] and "$12.50" in boxes["warning"][0]
    assert "Nothing was charged" in boxes["warning"][0]
    assert stock_repository.quantity_at(SHELF, "OIL") == 10  # no sale happened
    line = page._cart["OIL"]
    assert (line.unit_price, line.quantity) == (12.5, 2)  # the cart now shows the new price
    assert "$12.50" in page.cart_notice()

    page._checkout("Card")  # second try, at the visible price, goes through
    assert stock_repository.quantity_at(SHELF, "OIL") == 8
    assert boxes["info"] and "$25.00" in boxes["info"][-1]
    assert page.cart_notice() == ""


def test_reload_refreshes_prices_and_drops_withdrawn_products(page):
    page._add_to_cart(_tile_for(page, "OIL"))
    page._add_to_cart(_tile_for(page, "RICE"))
    product_repository.update(Product("OIL", "Oil", 11.0, 0, 2))
    product_repository.set_active("RICE", False)

    page.reload()

    assert page._cart["OIL"].unit_price == 11.0
    assert "RICE" not in page._cart
    assert "$11.00" in page.cart_notice() and "no longer sold" in page.cart_notice()


def test_reload_cuts_a_quantity_back_to_what_the_shelf_still_holds(page):
    page._add_to_cart(_tile_for(page, "OIL"))
    for _ in range(5):
        page._change_quantity("OIL", 1)
    assert page._cart["OIL"].quantity == 6
    stock_repository.dispatch(SHELF, "OIL", 7)  # someone sold / wrote off 7: 3 left

    page.reload()

    assert page._cart["OIL"].quantity == 3 and page._cart["OIL"].max_quantity == 3


def test_a_deactivated_product_while_in_the_cart_blocks_the_sale(page, boxes):
    page._add_to_cart(_tile_for(page, "RICE"))
    product_repository.set_active("RICE", False)

    page._checkout("Cash")

    assert boxes["warning"] and "no longer sold" in boxes["warning"][0]
    assert page._cart == {}
    assert stock_repository.quantity_at(SHELF, "RICE") == 10


def test_deactivated_products_are_not_in_the_grid_and_the_search_finds_barcodes(page):
    from PySide6.QtWidgets import QLabel

    def names():
        # Read the grid layout itself: tiles removed by a re-render are only deleteLater()'d, so they can
        # still be page children until the event loop runs.
        tiles = [page._grid_layout.itemAt(i).widget() for i in range(page._grid_layout.count())]
        return sorted(l.text() for t in tiles for l in t.findChildren(QLabel) if l.text() in ("Oil", "Rice", "Retired"))

    assert names() == ["Oil", "Rice"]  # "Retired" (inactive) isn't offered for sale
    page._search_input.setText("rice")
    assert names() == ["Rice"]
    page._search_input.setText("OIL")  # the scanned barcode, any case
    assert names() == ["Oil"]


def test_an_unchanged_cart_checks_out_normally(page, boxes):
    page._add_to_cart(_tile_for(page, "OIL"))
    page._checkout("Cash")
    assert boxes["warning"] == []
    assert stock_repository.quantity_at(SHELF, "OIL") == 9
    assert page._cart == {}
