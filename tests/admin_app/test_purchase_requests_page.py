"""Offscreen GUI tests for admin_app's Purchase Requests page and the
live sidebar badge."""

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
def held_orders():
    product_repository.create(Product("PLT-4410", "Pallet wrap 500mm", 900, 5, 10))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680))
    first = po_repo.submit("PLT-4410", "Kuzey", 40, 742.5, "WH-01")
    second = po_repo.submit("PLT-4410", "Kuzey", 10, 50, "WH-01")
    po_repo.submit("PLT-4410", "Kuzey", 10, 600, "WH-01")  # sent directly
    return first, second


@pytest.fixture
def page(qapp):
    from admin_app.gui.pages.purchase_requests_page import PurchaseRequestsPage

    widget = PurchaseRequestsPage()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_queue_lists_only_held_orders(held_orders, page):
    page.reload()
    assert [c.order.id for c in page._queue.cards()] == [held_orders[1].id, held_orders[0].id]
    assert page._pending_card._value_label.text() == "2"


def test_approve_from_the_card(held_orders, page, qapp):
    page.reload()
    card = next(c for c in page._queue.cards() if c.order.id == held_orders[0].id)
    card.approve_button.click()
    pump(qapp)

    assert po_repo.get(held_orders[0].id).status == "sent"
    assert "approved" in page._queue.last_action()
    assert [c.order.id for c in page._queue.cards()] == [held_orders[1].id]


def test_reject_with_reason(held_orders, page, qapp, monkeypatch):
    page.reload()
    monkeypatch.setattr(page, "_ask_reject_note", lambda number: ("Wrong unit", True))
    card = next(c for c in page._queue.cards() if c.order.id == held_orders[1].id)
    card.reject_button.click()
    pump(qapp)

    order = po_repo.get(held_orders[1].id)
    assert (order.status, order.decision_note) == ("rejected", "Wrong unit")


def test_cancelling_the_reject_prompt_changes_nothing(held_orders, page, qapp, monkeypatch):
    page.reload()
    monkeypatch.setattr(page, "_ask_reject_note", lambda number: ("", False))
    page._reject(held_orders[1].id)

    assert po_repo.get(held_orders[1].id).status == "pending"


def test_stale_decision_is_reported_not_raised(held_orders, page):
    page.reload()
    po_repo.reject(held_orders[0].id)  # another admin, first

    page._decide(held_orders[0].id, approve=True, note=None)

    assert "no longer awaiting approval" in page._queue.last_action()
    assert po_repo.get(held_orders[0].id).status == "rejected"


def test_setting_a_band_from_the_popup(page, qapp):
    product_repository.create(Product("LBL-0091", "Labels", 450, 3, 10))
    page.reload()
    page._open_add_range()
    popup = page._range_popup
    popup._product_input.setCurrentIndex(popup._product_input.findData("LBL-0091"))
    popup._min_input.setValue(390)
    popup._max_input.setValue(520)
    popup._validate_and_accept()
    pump(qapp)

    band = po_repo.get_price_range("LBL-0091")
    assert (band.min_unit_price, band.max_unit_price) == (390, 520)
    assert page._ranges_table.rowCount() == 1


def test_sidebar_badge_is_the_real_pending_count(held_orders, qapp):
    from admin_app.gui.main_window import MainWindow

    window = MainWindow()
    window._on_pending_polled(window._poll_pending_count())
    button = window._sidebar._buttons_by_key["purchase_requests"]
    assert button.text().endswith("2")

    po_repo.approve(held_orders[0].id)
    po_repo.approve(held_orders[1].id)
    window._on_pending_polled(window._poll_pending_count())
    assert button.text() == "Purchase requests"  # badge hidden at zero
    window.close()
