"""A computer that signed in to its server once asks any "server" to prove itself before it gets a PIN."""

from __future__ import annotations

import pytest

from database import account_repository as accounts
from database import remote, server_discovery, server_identity
from shared import auth, discovery, paths
from shared.server_client import insecure_problem
from tests.server_support import FakeServer


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def ours():
    s = FakeServer(secret="the real server's secret, 32+ chars")
    yield s
    s.close()


@pytest.fixture
def impostor():
    s = FakeServer(secret="somebody else's secret, also long")
    yield s
    s.close()


def _sign_in():
    return accounts.authenticate("B-001", "482915", auth.AREA_ADMIN, "Admin")


def test_the_first_sign_in_pairs_and_later_ones_check(ours):
    paths.set_server_url(ours.url)
    assert server_identity.proves_itself(ours.url) is None  # nothing to check against yet
    assert _sign_in().name == "Ali"
    pairing = paths.get_server_pairing()
    assert pairing and len(pairing["key"]) == 64
    assert server_identity.proves_itself(ours.url) is True
    remote.use_server(None)
    assert _sign_in().name == "Ali" and paths.get_server_pairing() == pairing  # paired once


def test_an_impostor_at_the_address_gets_no_pin(ours, impostor):
    paths.set_server_url(ours.url)
    _sign_in()
    remote.use_server(None)
    paths.set_server_url(impostor.url)  # something else now answers where the server was
    with pytest.raises(server_identity.ServerNotOursError):
        _sign_in()
    assert ("POST", "/auth/login") not in impostor.requests
    assert not remote.active()
    # root confirms a reinstalled server by typing its address on purpose: the pairing is forgotten
    paths.set_server_pairing(None)
    assert _sign_in().name == "Ali"


def test_a_server_that_does_not_answer_still_allows_the_offline_sign_in(ours):
    paths.set_server_url(ours.url)
    _sign_in()
    remote.use_server(None)
    ours.close()
    assert server_identity.proves_itself(ours.url) is None
    assert _sign_in().name == "Ali" and not remote.online()  # from the sign-in kept on this computer
    with pytest.raises(Exception):
        accounts.authenticate("B-001", "000000", auth.AREA_ADMIN, "Admin")


def test_a_moved_server_is_adopted_only_if_it_proves_itself(ours, impostor, monkeypatch):
    monkeypatch.delenv("GPUSA_DISCOVERY", raising=False)
    paths.set_server_url(ours.url)
    _sign_in()
    paths.set_server_url("http://127.0.0.1:1")  # the saved address stopped answering
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: impostor.url)
    assert server_discovery.adopt_server() is None and paths.get_server_url() == "http://127.0.0.1:1"
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: ours.url)
    assert server_discovery.adopt_server() == ours.url
    # a computer that never signed in has nothing to tell them apart by: it keeps its address
    paths.set_server_pairing(None)
    paths.set_server_url("http://127.0.0.1:1")
    assert server_discovery.adopt_server() is None


@pytest.mark.parametrize("url,ok", [
    ("http://192.168.1.20:8000", True), ("http://10.0.0.5", True), ("http://127.0.0.1:8000", True),
    ("http://100.101.102.103:8000", True), ("http://sunucu:8000", True), ("http://kasa.local", True),
    ("https://gpusa.example.com", True), ("http://gpusa.example.com", False), ("http://8.8.8.8:8000", False),
    ("file:///etc/passwd", False), ("ftp://10.0.0.5", False), ("http://", False),
])
def test_plain_http_only_inside_a_local_network(url, ok, monkeypatch):
    assert (insecure_problem(url) is None) is ok
    monkeypatch.setenv("GPUSA_ALLOW_HTTP", "1")
    assert (insecure_problem("http://8.8.8.8:8000") is None)
    assert insecure_problem("file:///etc/passwd") is not None
