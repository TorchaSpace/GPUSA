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

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(product_repository, "list_active", boom)
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


# --- receiving a delivery and cancelling (purchase order life after approval) ---------------

from database import stock_repository, warehouse_repository  # noqa: E402
from shared import current_session  # noqa: E402
from shared.models import StockLocation, Warehouse  # noqa: E402
from tests.po_support import ADMIN as PO_ADMIN, DEPOT, OTHER_DEPOT, seed_people, session_for  # noqa: E402

WH1 = StockLocation.warehouse("WH-01")


@pytest.fixture
def desk(qapp):
    """The purchasing panel for WH-01, signed in as a depot manager (conftest signs out afterwards)."""
    warehouse_repository.create(Warehouse(code="WH-01", name="Test", city="Tuzla", capacity_units=1000))
    product_repository.create(Product("PLT-4410", "Pallet wrap 500mm", 900, 0, 10))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680, "Kuzey Ambalaj A.Ş."))
    seed_people()
    current_session.set(session_for(DEPOT, "depot_manager"))
    from depot_app.gui.purchasing_panel import PurchasingPanel

    widget = PurchasingPanel("WH-01 · Test")
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _order(qty=100, price=600.0, raised_by=DEPOT):
    return po_repo.submit("PLT-4410", "Kuzey", qty, price, "WH-01 · Test", raised_by=raised_by)


def _row_of(panel, order):
    return next(r for r in range(panel._table.rowCount()) if panel._table.item(r, 0).text() == order.number)


def _status_of(panel, order):
    return panel._table.item(_row_of(panel, order), 7).text()


def test_status_text_shows_what_has_arrived():
    from depot_app.gui.purchasing_panel import status_text
    from shared.models import PurchaseOrder

    def order(status, received=0, decided=None):
        return PurchaseOrder("B", "n", "s", 100, 5.0, "WH", status, received_qty=received, decided_at=decided)

    assert status_text(order("pending")) == "Awaiting Admin Approval"
    assert status_text(order("sent")) == "Sent"
    assert status_text(order("sent", decided="2026-09-25T07:00:00.000Z")) == "Approved · Sent"
    assert status_text(order("partially_received", 40)) == "Partially received · 40 of 100"
    assert status_text(order("received", 100)) == "Received · 100 of 100"
    assert status_text(order("cancelled")) == "Cancelled"
    assert status_text(order("rejected")) == "Rejected"


def test_status_text_in_turkish():
    from depot_app.gui.purchasing_panel import status_text
    from shared import i18n
    from shared.models import PurchaseOrder

    i18n.set_language("tr")
    try:
        assert status_text(PurchaseOrder("B", "n", "s", 100, 5.0, "WH", "partially_received", received_qty=40)) == (
            "Kısmen teslim alındı · 40 / 100")
        assert status_text(PurchaseOrder("B", "n", "s", 100, 5.0, "WH", "cancelled")) == "İptal edildi"
    finally:
        i18n.set_language("en")


def test_warehouse_code_comes_from_the_site_label():
    from depot_app.gui.purchasing_panel import warehouse_code_of

    assert warehouse_code_of("WH-01 · İSTANBUL MERKEZ") == "WH-01"
    assert warehouse_code_of("wh-02") == "WH-02" and warehouse_code_of("") == ""


def test_only_approved_orders_with_goods_due_offer_receive(desk, qapp):
    sent = _order()
    held = _order(10, 742.5)
    partial = _order(50)
    po_repo.receive_against_order(partial.id, 20, DEPOT, WH1)
    done = _order(5)
    po_repo.receive_against_order(done.id, 5, DEPOT, WH1)
    cancelled = _order(5)
    po_repo.cancel_order(cancelled.id, PO_ADMIN)
    desk._refresh_orders_now()
    pump(qapp)

    assert desk.action_button("receive", sent.id) is not None
    assert desk.action_button("receive", partial.id) is not None
    for order in (held, done, cancelled):
        assert desk.action_button("receive", order.id) is None


def test_receiving_a_delivery_books_the_stock_and_the_row_follows(desk, qapp, monkeypatch):
    order = _order(100)
    desk._refresh_orders_now()
    asked = []
    monkeypatch.setattr(desk, "_ask_receive_quantity", lambda o: asked.append(o.id) or 40)

    desk.action_button("receive", order.id).click()
    pump(qapp)

    assert asked == [order.id]
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 40
    assert po_repo.get(order.id).status == "partially_received"
    assert _status_of(desk, order) == "Partially received · 40 of 100"
    assert desk._action_label.isVisibleTo(desk) and "40" in desk._action_label.text() and "WH-01" in desk._action_label.text()

    monkeypatch.setattr(desk, "_ask_receive_quantity", lambda o: o.remaining_qty)  # the rest arrives
    desk.action_button("receive", order.id).click()
    pump(qapp)

    assert stock_repository.quantity_at(WH1, "PLT-4410") == 100
    assert po_repo.get(order.id).status == "received"
    assert _status_of(desk, order) == "Received · 100 of 100"
    assert desk.action_button("receive", order.id) is None  # nothing left to receive
    assert "received in full" in desk._action_label.text()
    assert po_repo.get(order.id).received_by == "Deniz Depo · D-1"


def test_closing_the_dialog_receives_nothing(desk, qapp, monkeypatch):
    order = _order(100)
    desk._refresh_orders_now()
    monkeypatch.setattr(desk, "_ask_receive_quantity", lambda o: None)
    desk.action_button("receive", order.id).click()
    pump(qapp)
    assert po_repo.get(order.id).received_qty == 0 and stock_repository.quantity_at(WH1, "PLT-4410") == 0
    assert not desk._action_label.isVisibleTo(desk)


def test_a_refused_receipt_is_explained_and_changes_nothing(desk, qapp, monkeypatch):
    order = _order(100)
    desk._refresh_orders_now()
    warehouse = warehouse_repository.get_by_code("WH-01")
    warehouse.is_active = False
    warehouse_repository.update(warehouse)
    monkeypatch.setattr(desk, "_ask_receive_quantity", lambda o: 10)

    desk.action_button("receive", order.id).click()
    pump(qapp)

    assert "inactive" in desk._action_label.text() and order.number in desk._action_label.text()
    assert po_repo.get(order.id).received_qty == 0


def test_a_stale_quantity_that_would_over_receive_is_refused_with_the_remaining_units(desk, qapp, monkeypatch):
    order = _order(100)
    desk._refresh_orders_now()  # this screen thinks 100 are still due ...
    po_repo.receive_against_order(order.id, 80, OTHER_DEPOT, WH1)  # ... another console took 80
    monkeypatch.setattr(desk, "_ask_receive_quantity", lambda o: 50)

    desk.action_button("receive", order.id).click()
    pump(qapp)

    assert "only 20 units still due" in desk._action_label.text()
    assert po_repo.get(order.id).received_qty == 80 and stock_repository.quantity_at(WH1, "PLT-4410") == 80
    assert _status_of(desk, order) == "Partially received · 80 of 100"  # and the table caught up


def test_cancel_is_offered_only_where_the_person_may_cancel(desk, qapp):
    mine = _order(10, 742.5, raised_by=DEPOT)
    theirs = _order(10, 742.5, raised_by=OTHER_DEPOT)
    approved = _order(10, 600)
    desk._refresh_orders_now()
    assert desk.action_button("cancel", mine.id) is not None
    assert desk.action_button("cancel", theirs.id) is None  # someone else's held order
    assert desk.action_button("cancel", approved.id) is None  # only an administrator cancels an approved one

    current_session.set(session_for(PO_ADMIN, "admin"))
    desk._refresh_orders_now()
    assert desk.action_button("cancel", mine.id) is not None and desk.action_button("cancel", theirs.id) is not None
    assert desk.action_button("cancel", approved.id) is None  # this panel only cancels held orders

    current_session.clear()
    desk._refresh_orders_now()
    assert desk.action_button("cancel", mine.id) is None


def test_cancelling_a_held_order_asks_first(desk, qapp, monkeypatch):
    held = _order(10, 742.5)
    desk._refresh_orders_now()
    asked = []
    monkeypatch.setattr(desk, "_confirm_cancel", lambda o: asked.append(o.number) or False)
    desk.action_button("cancel", held.id).click()
    pump(qapp)
    assert asked == [held.number] and po_repo.get(held.id).status == "pending"

    monkeypatch.setattr(desk, "_confirm_cancel", lambda o: True)
    desk.action_button("cancel", held.id).click()
    pump(qapp)
    done = po_repo.get(held.id)
    assert done.status == "cancelled" and done.cancelled_by == "Deniz Depo · D-1"
    assert _status_of(desk, held) == "Cancelled"
    assert "cancelled" in desk._action_label.text() and held.number in desk._action_label.text()
    assert desk.action_button("cancel", held.id) is None


def test_the_awaiting_banner_gives_way_when_the_held_order_is_cancelled(desk, qapp):
    _fill(desk, qapp, "PLT-4410", 40, "742,50")
    desk._hold_button.click()
    pump(qapp)
    held = po_repo.list_pending()[0]
    assert desk._awaiting_banner.isVisibleTo(desk)

    po_repo.cancel_order(held.id, DEPOT)
    desk._refresh_orders_now()
    pump(qapp)

    assert not desk._awaiting_banner.isVisibleTo(desk)
    assert "cancelled" in desk._notice_text.text() and held.number in desk._notice_text.text()


def test_cancelling_the_order_in_the_awaiting_banner_returns_to_the_form(desk, qapp, monkeypatch):
    _fill(desk, qapp, "PLT-4410", 40, "742,50")
    desk._hold_button.click()
    pump(qapp)
    held = po_repo.list_pending()[0]
    monkeypatch.setattr(desk, "_confirm_cancel", lambda o: True)

    desk.action_button("cancel", held.id).click()
    pump(qapp)

    assert po_repo.get(held.id).status == "cancelled"
    assert not desk._awaiting_banner.isVisibleTo(desk) and desk._form.isVisibleTo(desk)


def test_an_approved_order_still_waiting_stays_listed_however_old(desk, qapp, monkeypatch):
    from depot_app.gui import purchasing_panel

    old_waiting = _order(10, 600)
    for _ in range(3):
        _order(10, 742.5)  # newer held orders
    monkeypatch.setattr(purchasing_panel, "_TABLE_LIMIT", 2)
    desk._refresh_orders_now()
    pump(qapp)

    numbers = [desk._table.item(r, 0).text() for r in range(desk._table.rowCount())]
    assert old_waiting.number in numbers and len(numbers) == 3  # the 2 newest + the one still waiting
    assert numbers == sorted(numbers, reverse=True)


def test_receive_dialog_limits_the_quantity_to_what_is_due(qapp):
    from PySide6.QtWidgets import QLabel

    from depot_app.gui.components.receive_delivery_dialog import ReceiveDeliveryDialog
    from shared.models import PurchaseOrder

    order = PurchaseOrder("PLT-4410", "Pallet wrap", "Kuzey", 100, 600.0, "WH-01", "partially_received",
                          id=7, received_qty=30)
    dialog = ReceiveDeliveryDialog(order, "WH-01")
    assert dialog.quantity() == 70  # defaults to everything still due
    spin = dialog._quantity_input
    assert (spin.minimum(), spin.maximum()) == (1, 70)
    spin.setValue(500)
    assert dialog.quantity() == 70  # can't type past what is due
    spin.setValue(0)
    assert dialog.quantity() == 1
    assert "PO-00007" in dialog.windowTitle()
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Kuzey" in texts and "WH-01" in texts and "70" in texts
    dialog.close()
