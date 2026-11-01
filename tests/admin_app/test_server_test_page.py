"""Offscreen GUI tests for admin_app's Server (test) page, against a fake server."""

from __future__ import annotations

import time

import pytest

from tests.gui_support import pump, qapp  # noqa: F401
from tests.server_support import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def _page(qapp, url):
    from admin_app.gui.pages.server_test_page import ServerTestPage

    page = ServerTestPage()
    page.url_edit.setText(url)
    page.show()
    pump(qapp)
    return page


def _wait(qapp, page, seconds=5.0):
    deadline = time.time() + seconds
    while page._busy and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    pump(qapp)
    assert not page._busy, "the request never finished"


def test_connect_lists_products_and_counts(qapp, server):
    page = _page(qapp, server.url)
    page.badge_edit.setText("B-001")
    page.pin_edit.setText("482915")
    page.connect_to_server()
    _wait(qapp, page)
    assert page.table.rowCount() == 2
    assert page.table.item(0, 1).text() == "Anvil" and page.table.item(0, 2).text() == "9.50"
    assert "2 products" in page.summary_label.text() and "1 employees" in page.summary_label.text()
    assert "Ali" in page.status_label.text()
    assert page.pin_edit.text() == ""  # the PIN is not kept
    assert page.refresh_button.isEnabled()
    page.close()


def test_wrong_pin_shows_the_servers_message_and_loads_nothing(qapp, server):
    page = _page(qapp, server.url)
    page.badge_edit.setText("B-001")
    page.pin_edit.setText("000000")
    page.connect_to_server()
    _wait(qapp, page)
    assert "Badge or PIN is wrong." in page.status_label.text()
    assert page.table.rowCount() == 0 and not page.refresh_button.isEnabled()
    page.close()


def test_unreachable_server_is_reported_not_raised(qapp):
    page = _page(qapp, "http://127.0.0.1:1")
    page.badge_edit.setText("B-001")
    page.pin_edit.setText("482915")
    page.connect_to_server()
    _wait(qapp, page)
    assert "Cannot reach the server" in page.status_label.text()
    page.close()


def test_a_cashier_who_may_not_list_employees_still_sees_the_rest(qapp):
    s = FakeServer(employees_allowed=False)
    try:
        page = _page(qapp, s.url)
        page.badge_edit.setText("B-001")
        page.pin_edit.setText("482915")
        page.connect_to_server()
        _wait(qapp, page)
        assert page.table.rowCount() == 2 and "- employees" in page.summary_label.text()
        page.close()
    finally:
        s.close()


def test_refresh_reloads(qapp, server):
    page = _page(qapp, server.url)
    page.badge_edit.setText("B-001")
    page.pin_edit.setText("482915")
    page.connect_to_server()
    _wait(qapp, page)
    before = len([r for r in server.requests if r == ("GET", "/products")])
    page.load_data()
    _wait(qapp, page)
    assert len([r for r in server.requests if r == ("GET", "/products")]) == before + 1
    page.close()
