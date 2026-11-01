"""Sign-in when this computer has a server address: the server checks the
badge and PIN and the app switches to server mode."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import account_repository as accounts, remote
from database.exceptions import SignInFailedError
from shared import auth, paths
from tests.server_support import FakeServer


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def server():
    s = FakeServer()
    paths.set_server_url(s.url)
    yield s
    s.close()


def test_sign_in_on_the_server_switches_server_mode_on(server):
    session = accounts.authenticate("b-001", "482915", auth.AREA_POS, "Till 1")
    assert (session.badge_id, session.name, session.role, session.area) == ("B-001", "Ali", "admin", "pos")
    assert session.account_id == 0  # no local account for this badge on this computer
    assert remote.active() and remote.server_url() == server.url
    assert ("POST", "/auth/login") in server.requests
    accounts.sign_out(session)
    assert not remote.active()


def test_wrong_pin_is_the_usual_error_and_nothing_switches(server):
    with pytest.raises(SignInFailedError):
        accounts.authenticate("B-001", "000000", auth.AREA_POS, "Till 1")
    assert not remote.active()


def test_pin_confirmation_checks_but_keeps_the_signed_in_person(server):
    accounts.authenticate("B-001", "482915", auth.AREA_POS, "Till 1")
    first = remote._client
    accounts.authenticate("B-001", "482915", auth.AREA_DEPOT_CONSOLE, "Till 1", event="pin_confirmed")
    assert remote._client is first


def test_unreachable_server_says_so():
    paths.set_server_url("http://127.0.0.1:1")
    with pytest.raises(remote.ServerUnavailableError):
        accounts.authenticate("B-001", "482915", auth.AREA_POS, "Till 1")


def test_local_account_id_is_kept_for_the_same_badge(server):
    paths.set_server_url(None)
    local = accounts.create_first_admin("B-001", "Ali Local", "482999")
    paths.set_server_url(server.url)
    session = accounts.authenticate("B-001", "482915", auth.AREA_ADMIN, "Admin")  # the SERVER's PIN
    assert session.account_id == local.account_id and session.name == "Ali"


def test_no_server_address_means_local_sign_in():
    accounts.create_first_admin("B-1", "Erol", "482913")
    assert accounts.authenticate("B-1", "482913", auth.AREA_ADMIN, "Admin").name == "Erol"
    assert not remote.active()
