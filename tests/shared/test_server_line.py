"""The server line on the sign-in screens: connecting never needs a separate page."""

from __future__ import annotations

import pytest

from database import account_repository
from shared import paths
from tests.gui_support import pump, qapp  # noqa: F401
from tests.server_support import FakeServer

PALETTE = {"background": "#111", "surface": "#222", "text_primary": "#eee", "text_secondary": "#aaa",
           "border": "#333", "accent": "#c96", "alert_critical": "#e55"}


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_the_line_takes_an_address_only_if_a_server_answers(qapp, server):
    from shared.gui_kit.server_line import ServerLine

    line = ServerLine(PALETTE)
    line.show()
    pump(qapp)
    seen = []
    line.changed.connect(seen.append)
    assert "No server" in line.status.text() and line._form.isHidden()
    line.toggle.click()
    assert not line._form.isHidden()
    line.address.setText("127.0.0.1:1")
    assert line.connect_to() is False and paths.get_server_url() is None and not seen
    assert "No GPUSA server" in line.message.text()
    line.address.setText(server.url)
    assert line.connect_to() is True and paths.get_server_url() == server.url and seen == [server.url]
    # connected: the address is not shown to whoever stands at the sign-in screen
    assert server.url not in line.status.text() and line.address.text() == "" and line._form.isHidden()
    line.close()


def test_an_address_from_the_environment_cannot_be_changed_here(qapp, server, monkeypatch):
    from shared.gui_kit.server_line import ServerLine

    monkeypatch.setenv(paths.SERVER_URL_ENV, "http://10.0.0.5:8000")
    paths.set_server_pairing({"device": "ab" * 16, "key": "cd" * 32})
    line = ServerLine(PALETTE)
    line.address.setText(server.url)
    assert line.connect_to() is False and "GPUSA_SERVER_URL" in line.message.text()
    assert paths.get_server_pairing() is not None
    # the same server typed again is "yes, this is our server": the next sign-in pairs afresh
    monkeypatch.setenv(paths.SERVER_URL_ENV, server.url)
    line.address.setText(server.url)
    assert line.connect_to() is True and paths.get_server_pairing() is None
    line.close()


def test_a_public_address_must_be_https(qapp):
    from shared.gui_kit.server_line import ServerLine

    line = ServerLine(PALETTE)
    line.address.setText("http://8.8.8.8:8000")
    assert line.connect_to() is False and "https" in line.message.text() and paths.get_server_url() is None
    line.close()


def test_only_the_main_sign_in_carries_the_line(qapp):
    from shared.gui_kit.sign_in_dialog import SignInDialog

    assert SignInDialog("t", "s", lambda b, p: None, PALETTE).server_line is None
    assert SignInDialog("t", "s", lambda b, p: None, PALETTE, server_line=True).server_line is not None


def test_first_run_offers_the_root_badge_and_follows_a_typed_server(qapp, server, monkeypatch):
    """Typing a server address on the first-run screen closes it, and sign_in() shows what fits that server:
    here it already has its root, so the badge + PIN sign-in comes next instead of 'create the root user'."""
    from admin_app.gui import auth_flow

    first = auth_flow.FirstAdminDialog()
    assert first.badge_input.text() == "B-001"
    first.close()
    first.deleteLater()

    shown = []
    monkeypatch.setattr(account_repository, "admin_exists", lambda: bool(paths.get_server_url()))

    def run(dialog):
        shown.append(type(dialog).__name__)
        if isinstance(dialog, auth_flow.FirstAdminDialog):
            dialog.server_line.address.setText(server.url)
            assert dialog.server_line.connect_to() is True
        return dialog.result()

    monkeypatch.setattr(auth_flow.QDialog, "exec", run)
    assert auth_flow.sign_in() is None
    assert shown == ["FirstAdminDialog", "SignInDialog"] and paths.get_server_url() == server.url
