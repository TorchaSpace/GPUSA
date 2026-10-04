"""Offscreen GUI tests for admin_app's Purchase Requests page and the
live sidebar badge."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import product_repository, purchase_order_repository as po_repo
from shared import current_session
from shared.auth import Actor
from shared.models import PriceRange, Product

ADMIN = Actor(badge_id="A-1", name="Ada Admin")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    monkeypatch.setattr(current_session, "actor", lambda: ADMIN)


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


def test_approve_from_the_card(held_orders, page, qapp, monkeypatch):
    page.reload()
    asked = []
    monkeypatch.setattr(page, "_confirm_approve", lambda order, order_id: asked.append(order) or True)
    card = next(c for c in page._queue.cards() if c.order.id == held_orders[0].id)
    card.approve_button.click()
    pump(qapp)

    assert [o.supplier for o in asked] == ["Kuzey"]
    assert po_repo.get(held_orders[0].id).status == "sent"
    assert po_repo.get(held_orders[0].id).decided_by == "Ada Admin · A-1"
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
    po_repo.reject(held_orders[0].id, decided_by=ADMIN)  # another admin, first

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

    po_repo.approve(held_orders[0].id, decided_by=ADMIN)
    po_repo.approve(held_orders[1].id, decided_by=ADMIN)
    window._on_pending_polled(window._poll_pending_count())
    assert button.text() == "Purchase requests"  # badge hidden at zero
    window.close()


# --- review fixes -------------------------------------------------------------


def test_declining_the_approve_confirmation_changes_nothing(held_orders, page, qapp, monkeypatch):
    page.reload()
    monkeypatch.setattr(page, "_confirm_approve", lambda order, order_id: False)
    card = next(c for c in page._queue.cards() if c.order.id == held_orders[0].id)
    card.approve_button.click()
    pump(qapp)

    assert po_repo.get(held_orders[0].id).status == "pending"


def test_confirmation_text_names_supplier_and_total(held_orders, page, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    page.reload()
    seen = {}

    def fake_question(parent, title, text, *args, **kwargs):
        seen["text"] = text
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    assert page._confirm_approve(po_repo.get(held_orders[0].id), held_orders[0].id) is False
    assert "Kuzey" in seen["text"] and "29,700.00" in seen["text"]


def test_decision_without_a_signed_in_admin_is_reported_and_changes_nothing(held_orders, page, monkeypatch):
    page.reload()
    monkeypatch.setattr(current_session, "actor", lambda: None)

    page._decide(held_orders[0].id, approve=True, note=None)

    assert "Couldn't save" in page._queue.last_action()
    assert po_repo.get(held_orders[0].id).status == "pending"


def test_a_database_error_shows_an_error_state_not_all_reviewed(held_orders, page, monkeypatch):
    import sqlite3

    page.reload()
    assert len(page._queue.cards()) == 2
    emitted = []
    page.pending_count_changed.connect(emitted.append)

    def boom():
        raise sqlite3.OperationalError("database is locked")

    real_list_orders = po_repo.list_orders
    monkeypatch.setattr(po_repo, "list_orders", lambda *a, **k: boom())
    page.reload()

    assert page._queue.cards() == []
    assert "locked" in page.load_error() and "locked" in page._queue.error_text()
    assert page._queue._empty_label.text() != "All requests reviewed."
    assert page._queue._empty_label.isVisibleTo(page._queue)
    assert page._pending_card._value_label.text() == "—"
    assert emitted == []  # the sidebar badge isn't told "0"

    monkeypatch.setattr(po_repo, "list_orders", real_list_orders)
    page.reload()
    assert page.load_error() is None and len(page._queue.cards()) == 2


def test_reload_if_pending_changed_compares_ids_not_just_the_count(held_orders, page):
    page.reload()
    assert page.reload_if_pending_changed() is False  # nothing changed

    po_repo.approve(held_orders[0].id, decided_by=ADMIN)
    newer = po_repo.submit("PLT-4410", "Kuzey", 5, 999, "WH-01")  # count still 2
    assert po_repo.count_pending() == 2

    assert page.reload_if_pending_changed() is True
    assert sorted(c.order.id for c in page._queue.cards()) == sorted([held_orders[1].id, newer.id])
    assert page.reload_if_pending_changed() is False


def test_reload_if_pending_changed_survives_a_failed_poll(held_orders, page, monkeypatch):
    import sqlite3

    page.reload()

    def boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(po_repo, "pending_ids", boom)
    assert page.reload_if_pending_changed() is False
    assert len(page._queue.cards()) == 2


def test_card_escapes_user_text_in_rich_labels(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from admin_app.gui.components.approval_queue import ApprovalCard
    from shared.models import PurchaseOrder

    order = PurchaseOrder("B", "<b>Name</b>", "<i>Sup</i>", 1, 5.0, "<img src=x>WH", "pending", id=1)
    card = ApprovalCard(order)
    meta = next(l for l in card.findChildren(QLabel) if l.text().startswith("<span"))
    assert "<img" not in meta.text() and "&lt;img src=x&gt;WH" in meta.text()
    for label in card.findChildren(QLabel):
        if "Name" in label.text() or "Sup" in label.text():
            assert label.textFormat() == Qt.PlainText


# --- the history after approval: received / partial / cancelled, and cancelling --------------

from database import stock_repository, warehouse_repository  # noqa: E402
from shared.models import StockLocation, Warehouse  # noqa: E402
from tests.po_support import DEPOT, seed_people  # noqa: E402

WH1 = StockLocation.warehouse("WH-01")


@pytest.fixture
def lifecycle():
    """One order in each state, the people with real accounts (Ada is the signed-in admin)."""
    warehouse_repository.create(Warehouse(code="WH-01", name="Test", capacity_units=1000))
    product_repository.create(Product("PLT-4410", "Pallet wrap 500mm", 900, 0, 10))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680))
    seed_people()
    orders = {}
    orders["held"] = po_repo.submit("PLT-4410", "Kuzey", 10, 742.5, "WH-01", raised_by=DEPOT)
    orders["waiting"] = po_repo.submit("PLT-4410", "Kuzey", 100, 600, "WH-01", raised_by=DEPOT)
    orders["partial"] = po_repo.submit("PLT-4410", "Kuzey", 100, 610, "WH-01", raised_by=DEPOT)
    po_repo.receive_against_order(orders["partial"].id, 40, DEPOT, WH1)
    orders["received"] = po_repo.submit("PLT-4410", "Kuzey", 5, 620, "WH-01", raised_by=DEPOT)
    po_repo.receive_against_order(orders["received"].id, 5, DEPOT, WH1)
    orders["cancelled"] = po_repo.submit("PLT-4410", "Kuzey", 7, 630, "WH-01", raised_by=DEPOT)
    po_repo.cancel_order(orders["cancelled"].id, ADMIN)
    orders["rejected"] = po_repo.submit("PLT-4410", "Kuzey", 3, 999, "WH-01", raised_by=DEPOT)
    po_repo.reject(orders["rejected"].id, "no", decided_by=ADMIN)
    return orders


def _row(page, order):
    table = page._orders_table
    return next(r for r in range(table.rowCount()) if table.item(r, 0).text() == order.number)


def _status(page, order):
    return page._orders_table.item(_row(page, order), 9).text()


def test_history_shows_every_status_with_what_has_arrived(lifecycle, page):
    page.reload()
    assert _status(page, lifecycle["held"]) == "Awaiting approval"
    assert _status(page, lifecycle["waiting"]) == "Sent"
    assert _status(page, lifecycle["partial"]) == "Partially received · 40 of 100"
    assert _status(page, lifecycle["received"]) == "Received · 5 of 5"
    assert _status(page, lifecycle["cancelled"]) == "Cancelled"
    assert _status(page, lifecycle["rejected"]) == "Rejected"
    tip = page._orders_table.item(_row(page, lifecycle["partial"]), 9).toolTip()
    assert "Received" in tip and "Deniz Depo · D-1" in tip
    tip = page._orders_table.item(_row(page, lifecycle["cancelled"]), 9).toolTip()
    assert "Cancelled" in tip and "Ada Admin · A-1" in tip


def test_status_filter_covers_the_new_states(lifecycle, page):
    page.reload()
    labels = [page._filter_input.itemText(i) for i in range(page._filter_input.count())]
    assert labels == ["All", "Awaiting approval", "Sent", "Partially received", "Received", "Rejected", "Cancelled"]
    for label, key in (("Partially received", "partial"), ("Received", "received"), ("Cancelled", "cancelled")):
        page._filter_input.setCurrentIndex(labels.index(label))
        assert page._orders_table.rowCount() == 1
        assert page._orders_table.item(0, 0).text() == lifecycle[key].number
    page._filter_input.setCurrentIndex(0)
    assert page._orders_table.rowCount() == 6


def test_delivered_card_counts_received_and_partial_orders(lifecycle, page):
    page.reload()
    assert page._delivered_card._value_label.text() == "2"
    assert page._delivered_full_item.layout().itemAt(1).widget().text() == "1"
    assert page._delivered_partial_item.layout().itemAt(1).widget().text() == "1"
    assert page._sent_card._value_label.text() == "1"  # only the one still waiting for goods


def test_cancel_is_offered_only_on_approved_orders_not_fully_received(lifecycle, page):
    page.reload()
    assert page.cancel_button(lifecycle["waiting"].id) is not None
    assert page.cancel_button(lifecycle["partial"].id) is not None
    for key in ("held", "received", "cancelled", "rejected"):
        assert page.cancel_button(lifecycle[key].id) is None


def test_cancelling_an_approved_order_asks_then_cancels(lifecycle, page, qapp, monkeypatch):
    page.reload()
    asked = []
    monkeypatch.setattr(page, "_confirm_cancel", lambda order: asked.append(order.number) or False)
    page.cancel_button(lifecycle["waiting"].id).click()
    pump(qapp)
    assert asked == [lifecycle["waiting"].number] and po_repo.get(lifecycle["waiting"].id).status == "sent"

    monkeypatch.setattr(page, "_confirm_cancel", lambda order: True)
    page.cancel_button(lifecycle["waiting"].id).click()
    pump(qapp)

    done = po_repo.get(lifecycle["waiting"].id)
    assert (done.status, done.cancelled_by) == ("cancelled", "Ada Admin · A-1")
    assert _status(page, lifecycle["waiting"]) == "Cancelled"
    assert page.cancel_button(lifecycle["waiting"].id) is None
    assert "cancelled" in page._queue.last_action() and lifecycle["waiting"].number in page._queue.last_action()


def test_cancelling_a_part_delivered_order_keeps_what_arrived(lifecycle, page, qapp, monkeypatch):
    page.reload()
    seen = {}
    from PySide6.QtWidgets import QMessageBox

    def fake_question(parent, title, text, *args, **kwargs):
        seen["text"] = text
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    page.cancel_button(lifecycle["partial"].id).click()
    pump(qapp)

    assert "40 of 100" in seen["text"] and "stay in stock" in seen["text"] and "60" in seen["text"]
    assert po_repo.get(lifecycle["partial"].id).status == "cancelled"
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 45  # 40 + 5 delivered earlier, all kept
    assert _status(page, lifecycle["partial"]) == "Cancelled"


def test_a_cancel_that_lost_a_race_is_reported_not_raised(lifecycle, page, qapp, monkeypatch):
    page.reload()
    monkeypatch.setattr(page, "_confirm_cancel", lambda order: True)
    po_repo.receive_against_order(lifecycle["waiting"].id, 100, DEPOT, WH1)  # the depot got there first
    page.cancel_button(lifecycle["waiting"].id).click()
    pump(qapp)

    assert "Couldn't cancel" in page._queue.last_action() and "received" in page._queue.last_action()
    assert po_repo.get(lifecycle["waiting"].id).status == "received"
    assert _status(page, lifecycle["waiting"]) == "Received · 100 of 100"  # and the table caught up


def test_cancel_without_a_signed_in_admin_changes_nothing(lifecycle, page, qapp, monkeypatch):
    page.reload()
    monkeypatch.setattr(page, "_confirm_cancel", lambda order: True)
    monkeypatch.setattr(current_session, "actor", lambda: None)
    page.cancel_button(lifecycle["waiting"].id).click()
    pump(qapp)
    assert "Couldn't cancel" in page._queue.last_action()
    assert po_repo.get(lifecycle["waiting"].id).status == "sent"


def test_status_labels_in_turkish(lifecycle, page):
    from shared import i18n

    i18n.set_language("tr")
    try:
        page.reload()
        assert _status(page, lifecycle["partial"]) == "Kısmen teslim alındı · 40 / 100"
        assert _status(page, lifecycle["cancelled"]) == "İptal edildi"
        assert page.cancel_button(lifecycle["waiting"].id).text() == "Siparişi iptal et"
    finally:
        i18n.set_language("en")
