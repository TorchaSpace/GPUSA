"""Admin's live side: every event kind reads in both languages, the bell counts and remembers,
toasts stay few, and a write from another connection refreshes the open page by itself."""

from __future__ import annotations

import pytest
from PySide6.QtTest import QTest

import database.connection as connection
from admin_app.gui.components.notification_center import (
    MAX_TOASTS, NotificationCenter, NotificationPanel, ToastHost, fill_activity_table, activity_table,
)
from database import activity_repository, product_repository, transaction_repository
from shared import i18n
from shared.activity import describe
from shared.models import ActivityEvent, LineItem, Product, Transaction
from tests.gui_support import pump, qapp  # noqa: F401

ALL_KINDS = (
    "sale", "stock_low", "stock_out", "refund", "day_close", "request_new", "request_declined",
    "shipment_created", "shipment_dispatched", "shipment_cancelled", "shipment_delivered", "shipment_short",
    "stock_in", "stock_written_out", "stock_count", "po_pending", "po_sent", "po_approved", "po_rejected",
    "account_locked",
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture(autouse=True)
def _english():
    before = i18n.current_language()
    yield
    i18n.set_language(before)


def _event(kind: str, severity: str = "info", **data) -> ActivityEvent:
    from datetime import datetime
    return ActivityEvent(id=1, at=datetime.now(), kind=kind, severity=severity, source="pos", location_kind=None,
                         location_code=None, location_name="Main", actor="Ayla", data=data)


@pytest.mark.parametrize("language", ["en", "tr"])
def test_every_kind_has_a_title_in_both_languages(language):
    i18n.set_language(language)
    for kind in ALL_KINDS:
        title, _detail = describe(_event(kind))
        assert title and not title.startswith("activity.") and title != kind.replace("_", " "), (language, kind)


def test_a_sale_reads_as_a_short_sentence():
    i18n.set_language("en")
    title, detail = describe(_event("sale", id=7, total=12.5, units=3, method="card"))
    assert "Main" in title or "Main" in detail
    assert "{" not in title + detail


def test_the_bell_counts_news_and_remembers_what_was_read(qapp):
    center = NotificationCenter("B-1")
    center.start()
    seen = []
    center.unread_changed.connect(seen.append)
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "sale", severity="info")
        activity_repository.record(conn, "refund", severity="notice")
        activity_repository.record(conn, "stock_out", severity="critical")
    arrived = center.poll()
    assert [e.kind for e in arrived] == ["sale", "refund", "stock_out"]
    assert center.unread == 2 and seen[-1] == 2  # routine sales do not ring the bell
    assert center.poll() == []
    center.mark_all_read()
    assert center.unread == 0
    again = NotificationCenter("B-1")
    again.start()
    assert again.unread == 0  # remembered per badge
    other = NotificationCenter("B-2")
    other.start()
    assert other.unread == 0  # a new window starts at "now"


def test_unread_from_an_earlier_session_still_counts(qapp):
    first = NotificationCenter("B-1")
    first.start()
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "refund", severity="notice")
    first.poll()
    first.mark_all_read()
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "stock_out", severity="critical")
    second = NotificationCenter("B-1")
    second.start()
    assert second.unread == 1


def test_panel_and_table_render_events(qapp):
    center = NotificationCenter()
    center.start()
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "sale", severity="info", total=5, units=1, method="cash", id=1)
        activity_repository.record(conn, "stock_out", severity="critical", product="Widget", barcode="W")
    panel = NotificationPanel(center)
    assert panel.table.rowCount() == 1  # "important only" is on
    panel.important_box.setChecked(False)
    assert panel.table.rowCount() == 2
    table = activity_table()
    fill_activity_table(table, center.recent(5))
    assert table.rowCount() == 2 and table.item(0, 1).text()
    panel.close()


def test_toasts_never_pile_up(qapp):
    from PySide6.QtWidgets import QWidget
    host_window = QWidget()
    host_window.resize(800, 600)
    host_window.show()
    host = ToastHost(host_window)
    for _ in range(MAX_TOASTS + 4):
        host.show_event(_event("stock_out", "critical", product="Widget"))
    assert len(host.active()) == MAX_TOASTS
    host_window.close()


def test_a_write_from_another_connection_refreshes_the_open_page(qapp):
    from admin_app.gui.main_window import MainWindow

    product_repository.create(Product("SKU-1", "Widget", 5.00, 50, 2))
    window = MainWindow()
    window._watcher.stop()
    window.show()
    pump(qapp)
    reloads = []
    page = window._overview_page
    original = page.reload
    page.reload = lambda: (reloads.append(1), original())
    window._watcher.start()
    transaction_repository.finalize_transaction(Transaction(items=[LineItem("SKU-1", "Widget", 5.00, 49)]))
    window._watcher._settle_ms = 10
    window._watcher._min_gap = 0
    for _ in range(40):
        QTest.qWait(50)
        if reloads:
            break
    assert reloads, "the page did not reload by itself"
    assert page._activity_table.rowCount() >= 1  # the sale and the 'low' warning are on Overview
    assert window._center.unread >= 1  # stock_low is a warning
    window.close()
