"""The 'server unreachable' strip of Admin and the Depot: only while server mode is on and not answering."""

from __future__ import annotations

import pytest

from database import remote
from shared.gui_kit.server_status import ServerStatusStrip
from shared.server_client import ServerError


class DownClient:
    base_url = "http://server.test"
    token = "tok"
    up = False

    def get(self, path):
        if not self.up:
            raise ServerError("no route", None)
        return {"badge_id": "B-001"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    yield
    remote.use_server(None)


def test_ping_follows_the_server():
    assert remote.ping() is True            # server mode off: nothing to report
    client = DownClient()
    remote.use_server(client)
    assert remote.ping() is False
    client.up = True
    assert remote.ping() is True            # no waiting out the short pause after a failure


def test_strip_shows_only_while_the_server_is_down(qapp):
    strip = ServerStatusStrip({})
    strip._timer.stop()
    qapp.processEvents()                    # its first check (a singleShot) runs now, with server mode off
    strip._show(False)
    assert strip.isHidden()                 # local database: never shown
    client = DownClient()
    remote.use_server(client)
    strip._show(False)
    assert not strip.isHidden() and strip.text()
    strip._show(True)
    assert strip.isHidden()
    strip.close()
    strip.deleteLater()
