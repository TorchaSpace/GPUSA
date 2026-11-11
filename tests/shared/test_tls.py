"""https to the server's own certificate: remembered at the first connection, only that one accepted after."""

from __future__ import annotations

import pytest

from shared import discovery, paths, tls
from shared.server_client import ServerClient, ServerError
from tests.server_support import FakeServer


_real_is_loopback = tls._is_loopback


@pytest.fixture(autouse=True)
def _over_the_network(monkeypatch):
    # The fakes listen on 127.0.0.1, which is trusted as it is; pretend it is another computer.
    monkeypatch.setattr(tls, "_is_loopback", lambda host: False)
    monkeypatch.setattr(tls, "publicly_trusted", lambda url, timeout=4.0: False)
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(paths.SERVER_URL_ENV, raising=False)


@pytest.fixture
def server_a():
    s = FakeServer(tls="a")
    yield s
    s.close()


@pytest.fixture
def server_b():
    s = FakeServer(tls="b")
    yield s
    s.close()


def test_the_first_connection_remembers_the_certificate(server_a):
    assert paths.get_server_cert() is None
    # not remembered yet: the normal check fails, a new server is only probed
    assert discovery.is_gpusa(server_a.url) is False
    assert discovery.is_gpusa(server_a.url, trust_new=True) is True
    url = discovery.reach(server_a.url.replace("https://", ""), 2.0)  # typed without https://: https is tried first
    assert url == server_a.url
    discovery.save_server(url, typed=True)
    assert paths.get_server_url() == server_a.url and paths.get_server_cert() == server_a.cert_pem
    assert discovery.is_gpusa(server_a.url) is True
    assert ServerClient(server_a.url).get("/health")["status"] == "ok"


def test_another_certificate_at_the_address_gets_nothing(server_a, server_b):
    discovery.save_server(server_a.url, typed=True)
    # an impostor at the same address shows another certificate: no request goes through
    paths.set_server_url(server_b.url)
    assert discovery.is_gpusa(server_b.url) is False
    with pytest.raises(ServerError, match="certificate"):
        ServerClient(server_b.url).post("/auth/login", {"badge_id": "B-001", "pin": "482915"})
    assert not [r for r in server_b.requests if r[1] == "/auth/login"]
    # found again on the network: the remembered certificate stays (it must still be shown)
    discovery.save_server(server_b.url, typed=False)
    assert paths.get_server_cert() == server_a.cert_pem
    # typed on purpose (the server was reinstalled): the new one is remembered
    discovery.save_server(server_b.url, typed=True)
    assert paths.get_server_cert() == server_b.cert_pem
    assert ServerClient(server_b.url).get("/health")["status"] == "ok"


def test_leaving_the_server_forgets_the_certificate(server_a):
    discovery.save_server(server_a.url, typed=True)
    discovery.save_server(None, typed=True)
    assert paths.get_server_url() is None and paths.get_server_cert() is None


def test_a_server_on_this_pc_needs_nothing_remembered(server_a, monkeypatch):
    monkeypatch.setattr(tls, "_is_loopback", _real_is_loopback)  # 127.0.0.1 is this PC again
    assert ServerClient(server_a.url).get("/health")["status"] == "ok"
    discovery.save_server(server_a.url, typed=True)
    assert paths.get_server_cert() is None


def test_a_public_ip_server_on_https_is_fine():
    from shared.server_client import insecure_problem

    assert insecure_problem("https://144.122.1.2:8000") is None
    assert insecure_problem("http://144.122.1.2:8000")
