"""Offscreen GUI tests for depot_app's Manager Portal purchasing tab."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import product_repository, purchase_order_repository as po_repo
from shared.models import PriceRange, Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def panel(qapp):
    product_repository.create(Product("PLT-4410", "Pallet wrap 500mm", 900, 5, 10))
    product_repository.create(Product("BOX-2218", "Carton", 40, 300, 200))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680, "Kuzey Ambalaj A.Ş."))
    from depot_app.gui.purchasing_panel import PurchasingPanel

    widget = PurchasingPanel("WH-01 · Test")
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _fill(panel, qapp, barcode, qty, price):
    panel._item_input.setCurrentIndex(panel._item_input.findData(barcode))
    panel._qty_input.setText(str(qty))
    panel._price_input.setText(price)
    pump(qapp)


def test_item_prefills_the_default_supplier(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 1, "")
    assert panel._supplier_input.text() == "Kuzey Ambalaj A.Ş."


def test_typed_supplier_is_not_overwritten_by_item_change(panel, qapp):
    _fill(panel, qapp, "BOX-2218", 1, "")
    panel._supplier_input.setText("My Supplier")
    _fill(panel, qapp, "PLT-4410", 1, "")
    assert panel._supplier_input.text() == "My Supplier"


def test_in_range_price_offers_send_and_sends(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 10, "600")
    assert panel._send_button.isVisibleTo(panel)
    assert not panel._hold_button.isVisibleTo(panel)

    panel._send_button.click()
    pump(qapp)

    order = po_repo.list_orders()[0]
    assert (order.status, order.site, order.quantity, order.unit_price) == ("sent", "WH-01 · Test", 10, 600)
    assert "sent" in panel._notice_text.text()


def test_comma_decimal_above_range_is_held_and_banner_follows_admin_approval(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 40, "742,50")
    assert "OUT OF SAFE RANGE" in panel._verdict_title.text()
    assert panel._hold_button.isVisibleTo(panel)

    panel._hold_button.click()
    pump(qapp)
    held = po_repo.list_pending()[0]
    assert held.unit_price == 742.5
    assert panel._awaiting_banner.isVisibleTo(panel)
    assert panel._awaiting_number.text() == held.number

    po_repo.approve(held.id)  # an admin, elsewhere
    panel._refresh_orders_now()  # what the poller does every few seconds
    pump(qapp)

    assert not panel._awaiting_banner.isVisibleTo(panel)
    assert "approved by an administrator" in panel._notice_text.text()


def test_product_without_band_is_held(panel, qapp):
    _fill(panel, qapp, "BOX-2218", 5, "38")
    assert "NO SAFE RANGE SET" in panel._verdict_title.text()
    assert panel._hold_button.isVisibleTo(panel)


def test_bad_quantity_shows_an_error_and_submits_nothing(panel, qapp):
    _fill(panel, qapp, "PLT-4410", "abc", "600")
    panel._send_button.click()
    pump(qapp)
    assert "quantity" in panel._error_label.text().lower()
    assert po_repo.list_orders() == []
