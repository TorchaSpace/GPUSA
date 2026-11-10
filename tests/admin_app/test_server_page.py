"""Offscreen GUI tests for Admin > Server (root only), against a fake server: nothing to sign in to on the
page - it shows the connection the sign-in made."""

from __future__ import annotations

import time

import pytest

from database import remote
from shared import auth, current_session, discovery, paths
from shared.server_client import ServerClient
from tests.gui_support import pump, qapp  # noqa: F401
from tests.server_support import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def _signed_in(server) -> None:
    client = ServerClient(server.url)
    who = client.login("B-001", "482915")
    remote.use_server(client)
    paths.set_server_url(server.url)
    current_session.set(auth.Session(account_id=1, badge_id=who["badge_id"], name=who["name"], role="admin", area="admin",
                                     terminal="Admin", signed_in_at="2026-11-10T10:00:00Z", is_root=True))


def _page(qapp):
    from admin_app.gui.pages.server_page import ServerPage

    page = ServerPage()
    page.show()
    pump(qapp)
    deadline = time.time() + 5.0
    while page._busy and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    pump(qapp)
    assert not page._busy, "the request never finished"
    return page


def test_shows_the_connection_by_itself(qapp, server):
    _signed_in(server)
    page = _page(qapp)
    v = {key: label.text() for key, label in page.values.items()}
    assert v["address"] == server.url and v["status"] == "Connected"
    assert v["version"] == "9.9" and v["database"] == "Working"
    assert "Ali" in v["signed_in"] and "B-001" in v["signed_in"]
    assert "2 products" in v["holds"] and "1 employees" in v["holds"] and "- accounts" in v["holds"]
    assert not hasattr(page, "pin_edit")  # no second sign-in on this page
    page.close()


def test_an_unreachable_server_is_said_not_raised(qapp):
    paths.set_server_url("http://127.0.0.1:1")
    page = _page(qapp)
    assert "Not answering" in page.values["status"].text()
    page.close()


def test_without_a_server_it_says_so_and_asks_nothing(qapp, server):
    page = _page(qapp)
    assert page.values["address"].text() == "No server"
    assert "this computer" in page.values["status"].text()
    assert ("GET", "/health") not in server.requests
    page.close()


def test_root_types_another_address(qapp, server, monkeypatch):
    page = _page(qapp)
    page.address_edit.setText("127.0.0.1:1")
    assert page.save_address() is False and paths.get_server_url() is None  # nothing answers there
    assert "No GPUSA server" in page.change_message.text() and page.restart_button.isHidden()
    page.address_edit.setText(server.url.removeprefix("http://"))
    assert page.save_address() is True and paths.get_server_url() == server.url
    assert not page.restart_button.isHidden()
    # an empty box takes this computer off the server again
    assert page.save_address() is True and paths.get_server_url() is None
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: server.url)
    page.find_on_network()
    assert page.address_edit.text() == server.url
    page.close()
