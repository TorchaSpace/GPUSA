"""Admin sets each shop's own reorder level; opening stock is typed per shop."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import dealership_repository, product_repository, stock_repository
from shared.models import UNASSIGNED, Dealership, Product, StockLocation
from tests.gui_support import pump, qapp  # noqa: F401

SHOP = StockLocation.dealership("D-A")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    dealership_repository.create(Dealership(code="D-A", name="Harbor", region="Metro", city="X"))
    product_repository.create(Product("W", "Widget", 5, 0, 10))
    product_repository.create(Product("G", "Gadget", 5, 0, 4))


def test_the_popup_saves_only_changed_rows_and_default_clears_an_override(qapp):
    from admin_app.gui.components.reorder_levels_popup import DEFAULT, ReorderLevelsPopup

    popup = ReorderLevelsPopup()
    popup.open_for(SHOP, "Harbor")
    assert popup.changes() == []
    spins = {barcode: spin for barcode, _saved, spin in popup._rows}
    spins["W"].setValue(25)
    assert popup.changes() == [("W", 25)]
    assert popup.save() == 1
    assert stock_repository.reorder_overrides_at(SHOP) == {"W": 25}
    spins = {barcode: spin for barcode, _saved, spin in popup._rows}
    assert spins["W"].value() == 25 and spins["G"].value() == DEFAULT
    spins["W"].setValue(DEFAULT)
    assert popup.save() == 1 and stock_repository.reorder_overrides_at(SHOP) == {}
    popup.close()


def test_dealership_page_buttons_open_the_two_tools_for_the_selected_shop(qapp):
    from admin_app.gui.pages.dealerships_page import DealershipsPage

    page = DealershipsPage()
    page.show()
    pump(qapp)
    assert not page._detail_levels_button.isEnabled()
    page.show_dealership("D-A")
    assert page._detail_levels_button.isEnabled() and page._detail_opening_button.isEnabled()
    page._open_reorder_levels()
    assert page._reorder_popup.isVisible() and "Harbor" in page._reorder_popup.windowTitle()
    # opening stock: counting the shop's shelf adds the units there (nothing was waiting unplaced)
    page._open_opening_stock()
    assert page._count_popup.isVisible() and page._count_popup._count_radio.isChecked()
    stock_repository.set_count(SHOP, "W", 12)
    assert stock_repository.quantity_at(SHOP, "W") == 12 and product_repository.get_by_barcode("W").stock_quantity == 12
    page._reorder_popup.close()
    page._count_popup.close()
    page.close()
