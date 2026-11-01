"""POS sync strip: hidden on a local till; says how many sales wait or were refused."""

from __future__ import annotations

import time

import pytest

import database.connection as connection
from database import remote
from tests.gui_support import pump, qapp  # noqa: F401


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _wait(qapp, banner):
    deadline = time.time() + 5
    while banner._busy and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    pump(qapp)


def test_hidden_when_the_till_is_local(qapp):
    from pos_app.gui.sync_banner import SyncBanner

    banner = SyncBanner({})
    banner.sync_now()
    assert not banner.isVisible() and not banner._busy


def test_shows_waiting_and_refused_sales(qapp, monkeypatch):
    from database import remote_sales
    from pos_app.gui.sync_banner import SyncBanner

    class Dead:
        base_url, token = "http://x", "t"

    remote.use_server(Dead())
    monkeypatch.setattr(remote_sales, "send_pending", lambda: {"sent": 0, "rejected": 0, "pending": 3})
    monkeypatch.setattr(remote_sales, "rejected_sales", lambda: [{}])
    banner = SyncBanner({})
    banner.show()
    banner.sync_now()
    _wait(qapp, banner)
    assert "3" in banner.text() and "1" in banner.text() and banner.isVisible()
