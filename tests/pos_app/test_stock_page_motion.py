"""Offscreen tests for the animated My Local Stock list and the Receive page's
custom widgets. Animations are off on the offscreen platform, so every widget
must already be in its final state right after the call - these check that
end state (what the tweens would arrive at)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships, stock_repository
from shared.models import Dealership, Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _stock_page(qapp, monkeypatch, products):
    from pos_app.gui.pages.my_stock_page import MyStockPage

    monkeypatch.setattr(stock_repository, "products_at", lambda _location: list(products))
    page = MyStockPage()
    page.show()
    pump(qapp)
    return page


_PRODUCTS = [
    Product("OUT-1", "Olive oil", 10, 0, 5),
    Product("LOW-1", "Lentils", 10, 3, 5),
    Product("OK-1", "Rice", 10, 50, 5),
    Product("OK-2", "Salt", 10, 40, 5),
]


def test_filter_counts_and_rows(qapp, monkeypatch):
    page = _stock_page(qapp, monkeypatch, _PRODUCTS)
    assert page._filter._counts == {"All": 4, "Low": 1, "Out": 1}
    assert len(page._rows) == 4

    page._set_filter("Low")
    assert [row.qty_label.text() for row in page._rows] == ["3"]
    assert page._filter.active_key() == "Low"

    page._set_filter("Out")
    assert [row.qty_label.text() for row in page._rows] == ["0"]


def test_rows_show_final_numbers_and_bar_percentages(qapp, monkeypatch):
    page = _stock_page(qapp, monkeypatch, _PRODUCTS)
    by_qty = {row.qty_label.text(): row for row in page._rows}
    assert set(by_qty) == {"0", "3", "50", "40"}
    assert by_qty["0"].bar.current_pct() == 0
    assert by_qty["3"].bar.current_pct() == 30  # 3 of 2x the reorder point (10)
    assert by_qty["50"].bar.current_pct() == 100  # capped


def test_reload_picks_up_new_quantities(qapp, monkeypatch):
    products = list(_PRODUCTS)
    page = _stock_page(qapp, monkeypatch, products)
    products[0] = Product("OUT-1", "Olive oil", 10, 8, 5)
    page.reload()
    assert page._filter._counts["Out"] == 0
    assert sorted(row.qty_label.text() for row in page._rows) == ["3", "40", "50", "8"]


def test_filter_segment_highlight_follows_the_active_item(qapp, monkeypatch):
    page = _stock_page(qapp, monkeypatch, _PRODUCTS)
    segment = page._filter
    segment.set_active("Out", animate=False)
    assert segment._x == pytest.approx(segment._cell(2)[0])
    segment.set_active("All", animate=False)
    assert segment._x == pytest.approx(segment._cell(0)[0])


@pytest.fixture
def shipment():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("A", "Oil", 10, 50, 5))
    product_repository.create(Product("B", "Rice", 10, 50, 5))
    s = ships.create("WH-01", "001", "Ridgeline", datetime.now() + timedelta(minutes=30), [("A", 24), ("B", 18)], "Murat")
    ships.dispatch(s.id)
    return s


def _receive_page(qapp):
    from pos_app.gui.pages.receive_page import ReceivePage

    page = ReceivePage("001", "Harbor Point")
    page.show()
    pump(qapp)
    return page


def test_check_toggle_and_row_background_follow_the_state(qapp, shipment):
    from pos_app.gui.pages import receive_page as rp

    page = _receive_page(qapp)
    row = page._rows["A"]
    assert not row.check.is_on() and row._to == row._plain

    page.toggle_line("A")
    assert page._rows["A"] is row  # the same widget moved to its new state
    assert row.check.is_on() and row._to == rp._ROW_CHECKED
    assert row.check._on.value == 1.0

    page._toggle_report()
    page.change_received("B", -2)
    assert page._rows["B"]._to == rp._ROW_ISSUE
    assert page._rows["B"].value.text() == "16"
    assert page._rows["B"].minus.isHidden() is False


def test_footer_count_keeps_its_template_and_the_complete_button_text(qapp, shipment):
    page = _receive_page(qapp)
    page.toggle_line("A")
    assert page._count_label.full_text().startswith("<b>1 ")
    assert page._count_label.text() == "1"
    page.change_received("B", -1)
    assert "1 discrepancy" in page._count_label.full_text()
    assert page._complete_button.text() == "Send report && receive"
    assert page._complete_button._shown_text() == "Send report & receive"


def test_selecting_a_card_swaps_the_selection(qapp, shipment):
    second = ships.create("WH-01", "001", "Other", datetime.now() + timedelta(hours=2), [("A", 5)])
    ships.dispatch(second.id)
    page = _receive_page(qapp)
    assert len(page._cards) == 2
    page.select(second.id)
    assert page.selected().id == second.id
    assert list(page._rows) == ["A"]
    assert page._cards[second.id]._sel.value == 1.0
    assert page._cards[shipment.id]._sel.value == 0.0
