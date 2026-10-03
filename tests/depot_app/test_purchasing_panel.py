"""Offscreen GUI tests for depot_app's Manager Portal purchasing tab."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import product_repository, purchase_order_repository as po_repo
from shared.auth import Actor
from shared.models import PriceRange, Product

ADMIN = Actor(badge_id="A-1", name="Ada Admin")


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
    assert (order.status, order.site, order.quantity, order.unit_price) == ("sent", "WH-01 · TEST", 10, 600)  # sites are stored upper-cased
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

    po_repo.approve(held.id, decided_by=ADMIN)  # an admin, elsewhere
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


# --- review fixes -------------------------------------------------------------


def _table_texts(panel, column):
    return [panel._table.item(r, column).text() for r in range(panel._table.rowCount())]


def test_a_rejection_note_stays_visible_in_the_orders_table(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 40, "742,50")
    panel._hold_button.click()
    pump(qapp)
    held = po_repo.list_pending()[0]

    po_repo.reject(held.id, "Wrong unit - re-quote per kg", decided_by=ADMIN)
    panel._refresh_orders_now()
    pump(qapp)
    panel._show_form()  # the banner is gone; the note must not be
    pump(qapp)

    assert _table_texts(panel, 8) == ["Wrong unit - re-quote per kg"]
    tooltip = panel._table.item(0, 7).toolTip()
    assert "Rejected" in tooltip and "Ada Admin · A-1" in tooltip and "Wrong unit" in tooltip
    assert panel._table.item(0, 8).toolTip() == tooltip


def test_undecided_and_unnoted_orders_have_an_empty_note_cell(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 10, "600")
    panel._send_button.click()
    pump(qapp)
    assert _table_texts(panel, 8) == [""]
    assert panel._table.item(0, 7).toolTip() == ""


def test_decision_tooltip_text():
    from depot_app.gui.purchasing_panel import decision_tooltip
    from shared.models import PurchaseOrder

    order = PurchaseOrder("B", "n", "s", 1, 5.0, "WH", "sent", decided_at="2026-09-25T07:01:46.123Z",
                          decided_by="Ada · A-1", decision_note="ok")
    text = decision_tooltip(order)
    assert text.startswith("Approved ") and "by Ada · A-1" in text and text.endswith("Note: ok")
    assert decision_tooltip(PurchaseOrder("B", "n", "s", 1, 5.0, "WH", "pending")) == ""


def test_rejected_banner_escapes_the_admin_note_and_supplier(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 40, "742,50")
    panel._supplier_input.setText("<i>Evil</i> Ltd")
    panel._hold_button.click()
    pump(qapp)
    held = po_repo.list_pending()[0]
    po_repo.reject(held.id, "<script>x</script>", decided_by=ADMIN)
    panel._refresh_orders_now()
    pump(qapp)

    text = panel._notice_text.text()
    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "<i>Evil</i>" not in text and "&lt;i&gt;Evil&lt;/i&gt;" in text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("5", 5), (" 40 ", 40), ("007", 7), ("1000000", 1_000_000), ("1", 1),
        ("0", None), ("", None), ("  ", None), ("-3", None), ("2.5", None), ("1e3", None), ("abc", None),
        ("1000001", None), ("9" * 50, None), ("0" * 500 + "1", None),
        ("\u00b2", None),  # superscript two: str.isdigit() is True, int() raises
        ("\u0663", None),  # Arabic-Indic three
        ("\uff13", None),  # fullwidth three
    ],
)
def test_parse_quantity(text, expected):
    from depot_app.gui.purchasing_panel import parse_quantity

    assert parse_quantity(text) == expected


def test_superscript_quantity_shows_an_error_not_a_crash(panel, qapp):
    _fill(panel, qapp, "PLT-4410", "\u00b2", "600")
    panel._send_button.click()
    pump(qapp)
    assert "quantity" in panel._error_label.text().lower()
    assert po_repo.list_orders() == []


def test_huge_quantity_is_refused_with_the_limit_named(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 1_000_001, "600")
    panel._send_button.click()
    pump(qapp)
    assert "1,000,000" in panel._error_label.text()
    assert po_repo.list_orders() == []


@pytest.mark.parametrize("price", ["nan", "inf", "1e999", "0", "0,004", "5000000000", "-5", "abc"])
def test_unusable_prices_show_an_error_and_submit_nothing(panel, qapp, price):
    _fill(panel, qapp, "PLT-4410", 10, price)
    panel._send_button.click()
    panel._hold_button.click()
    pump(qapp)
    assert panel._error_label.isVisibleTo(panel)
    assert po_repo.list_orders() == []


def test_preview_total_and_band_use_the_cent_rounded_price(panel, qapp):
    _fill(panel, qapp, "PLT-4410", 3, "680,0049")  # rounds to 680.00: inside the band (inclusive)
    assert panel._send_button.isVisibleTo(panel)
    assert panel._total_label.text() == "2,040.00"

    _fill(panel, qapp, "PLT-4410", 3, "1,005")  # comma + exactly 3 digits = a thousands mark: 1005
    assert panel._total_label.text() == "3,015.00"
    assert panel._hold_button.isVisibleTo(panel)  # far above the 680 ceiling


def test_a_failed_catalog_refresh_keeps_the_products_and_says_so(panel, qapp, monkeypatch):
    import sqlite3

    def boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(product_repository, "list_all", boom)
    panel.reload_catalog()

    assert panel._item_input.count() == 2
    assert "locked" in panel._error_label.text()
    assert panel._error_label.isVisibleTo(panel)


def test_a_raw_sqlite_error_on_submit_is_shown_not_raised(panel, qapp, monkeypatch):
    import sqlite3

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(po_repo, "submit", boom)
    _fill(panel, qapp, "PLT-4410", 10, "600")
    panel._send_button.click()
    pump(qapp)
    assert "locked" in panel._error_label.text()
