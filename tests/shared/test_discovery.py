"""A new PC finds the server on the network: UDP question, JSON answer, believed only if /health agrees."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from database import server_discovery
from shared import discovery, paths


class FakeServer:
    """Answers the discovery question on a loopback UDP port like server/gpusa_server/discovery.py does."""

    def __init__(self, http_port: int = 8000, answer: bytes | None = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.answer = answer if answer is not None else json.dumps({"gpusa": 1, "port": http_port}).encode()
        self.asked = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, sender = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if data == discovery.QUESTION:
                self.asked += 1
                self.sock.sendto(self.answer, sender)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self.sock.close()


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: True)
    s = FakeServer(8123)
    yield s
    s.close()


def test_finds_the_server_and_its_api_port(server):
    assert discovery.find_servers(server.port, 1.0, ["127.0.0.1"]) == ["http://127.0.0.1:8123"]
    assert discovery.find_server(server.port, 1.0, ["127.0.0.1"]) == "http://127.0.0.1:8123"


def test_nothing_answers_means_no_server():
    quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    quiet.bind(("127.0.0.1", 0))
    try:
        assert discovery.find_server(quiet.getsockname()[1], 0.4, ["127.0.0.1"]) is None
    finally:
        quiet.close()


def test_a_reply_is_believed_only_if_health_agrees(monkeypatch):
    s = FakeServer(8123)
    try:
        monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: False)
        assert discovery.find_servers(s.port, 0.6, ["127.0.0.1"]) == []
    finally:
        s.close()


@pytest.mark.parametrize("junk", [b"hello", b'{"gpusa": 1}', b'{"gpusa": 2, "port": 8000}',
                                  b'{"gpusa": 1, "port": 0}', b'{"gpusa": 1, "port": "x"}'])
def test_garbage_answers_are_ignored(monkeypatch, junk):
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: True)
    s = FakeServer(answer=junk)
    try:
        assert discovery.find_servers(s.port, 0.5, ["127.0.0.1"]) == []
    finally:
        s.close()


def test_two_servers_are_not_guessed(monkeypatch):
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: True)
    monkeypatch.setattr(discovery, "find_servers", lambda *a, **k: ["http://10.0.0.5:8000", "http://10.0.0.9:8000"])
    assert discovery.find_server() is None


def test_a_server_on_this_pc_is_one_server(monkeypatch):
    monkeypatch.setattr(discovery, "find_servers", lambda *a, **k: ["http://192.168.1.7:8000", "http://127.0.0.1:8000"])
    assert discovery.find_server() == "http://127.0.0.1:8000"


@pytest.fixture()
def saved(monkeypatch):
    store = {}
    monkeypatch.setattr(paths, "get_server_url", lambda: store.get("url"))
    monkeypatch.setattr(paths, "set_server_url", lambda url: store.__setitem__("url", url))
    monkeypatch.delenv("GPUSA_DISCOVERY", raising=False)
    monkeypatch.delenv(paths.SERVER_URL_ENV, raising=False)
    return store


def test_any_pc_without_an_address_adopts_the_server(monkeypatch, saved):
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: "http://10.0.0.5:8000")
    # opt-out
    monkeypatch.setenv("GPUSA_DISCOVERY", "0")
    assert server_discovery.adopt_server() is None and "url" not in saved
    monkeypatch.delenv("GPUSA_DISCOVERY")
    # found: saved, and not searched again while that address answers
    assert server_discovery.adopt_server() == "http://10.0.0.5:8000"
    assert saved["url"] == "http://10.0.0.5:8000"
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: True)
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: pytest.fail("searched again"))
    assert server_discovery.adopt_server() is None
    # nothing found: nothing saved
    saved.clear()
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: None)
    assert server_discovery.adopt_server() is None and "url" not in saved


def test_a_server_that_moved_is_found_again(monkeypatch, saved):
    from database import server_identity

    saved["url"] = "http://10.0.0.5:8000"
    monkeypatch.setattr(server_identity, "proves_itself", lambda url: True)  # see tests/database/test_server_identity.py
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: False)
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: "http://10.0.0.9:8000")
    assert server_discovery.adopt_server() == "http://10.0.0.9:8000" and saved["url"] == "http://10.0.0.9:8000"
    # the server is simply off: the saved address stays
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: None)
    assert server_discovery.adopt_server() is None and saved["url"] == "http://10.0.0.9:8000"


def test_an_address_from_the_environment_is_never_second_guessed(monkeypatch, saved):
    monkeypatch.setenv(paths.SERVER_URL_ENV, "http://10.0.0.5:8000")
    monkeypatch.setattr(discovery, "is_gpusa", lambda url, timeout=2.0, **kw: pytest.fail("checked"))
    monkeypatch.setattr(discovery, "find_server", lambda *a, **k: pytest.fail("searched"))
    assert server_discovery.adopt_server() is None


# --- an address typed by hand ------------------------------------------------------------------------------


@pytest.fixture
def api():
    from tests.server_support import FakeServer

    s = FakeServer()
    yield s
    s.close()


def test_https_typed_for_a_local_server_that_speaks_http_still_connects(api):
    # what a browser teaches: "https://IP:8000". The server PC runs plain http, so the TLS hello only gets
    # the connection reset; on a local address the apps may use http, so that is what answers and is saved.
    https = api.url.replace("http://", "https://")
    assert discovery.is_gpusa(https, 2.0) is False
    assert discovery.reach(https, 2.0) == api.url
    assert discovery.reach(api.url, 2.0) == api.url
    assert discovery.reach(api.url + "/", 2.0) == api.url


def test_an_address_without_the_port_gets_the_server_port(api, monkeypatch):
    port = int(api.url.rsplit(":", 1)[1])
    monkeypatch.setattr(discovery, "DEFAULT_API_PORT", port)
    assert discovery.reach("127.0.0.1", 2.0) == api.url
    assert discovery.reach("https://127.0.0.1", 2.0) == api.url


def test_nothing_answering_reaches_nothing():
    assert discovery.reach("127.0.0.1:1", 1.0) is None
    assert discovery.reach("", 1.0) is None


def test_an_internet_https_address_is_never_turned_into_http(monkeypatch):
    asked = []
    monkeypatch.setattr(discovery, "_health_ok", lambda url, timeout, context=None: asked.append(url) or False)
    assert discovery.reach("https://8.8.8.8:8000", 1.0) is None
    assert asked == ["https://8.8.8.8:8000"]


def test_an_internet_server_on_plain_http_is_told_apart_from_no_server(monkeypatch):
    asked = []
    monkeypatch.setattr(discovery, "_health_ok", lambda url, timeout, context=None: asked.append(url) or True)
    assert discovery.only_plain_http_on_internet("https://8.8.8.8:8000") is True
    assert discovery.only_plain_http_on_internet("8.8.8.8") is True
    assert asked == ["http://8.8.8.8:8000", "http://8.8.8.8:8000"]
    # a local address is no such case: http is fine there and reach() already tried it
    assert discovery.only_plain_http_on_internet("https://192.168.1.20:8000") is False
