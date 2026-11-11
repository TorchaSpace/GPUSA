import pytest

from shared.server_client import ServerClient, ServerError, normalize_url
from tests.server_support import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


def test_normalize_url():
    assert normalize_url(" 192.168.1.5:8000/ ") == "http://192.168.1.5:8000"
    assert normalize_url("https://api.example.com/") == "https://api.example.com"
    assert normalize_url("   ") == ""


def test_login_keeps_token_and_get_sends_it(server):
    client = ServerClient(server.url)
    who = client.login("B-001", "482915")
    assert who["name"] == "Ali" and client.token == "tok"
    assert [p["barcode"] for p in client.get("/products")] == ["A1", "B2"]


def test_wrong_pin_gives_the_servers_message_and_no_token(server):
    client = ServerClient(server.url)
    with pytest.raises(ServerError) as info:
        client.login("B-001", "000000")
    assert str(info.value) == "Badge or PIN is wrong." and info.value.status == 401
    assert client.token is None


def test_calls_without_a_token_are_refused(server):
    with pytest.raises(ServerError) as info:
        ServerClient(server.url).get("/products")
    assert info.value.status == 401


def test_unreachable_server_is_a_readable_error():
    with pytest.raises(ServerError) as info:
        ServerClient("http://127.0.0.1:1", timeout=1).login("B-001", "482915")
    assert "Cannot reach the server" in str(info.value)


def test_empty_address_is_asked_for():
    with pytest.raises(ServerError):
        ServerClient("").get("/products")


def test_a_refused_connection_is_explained_in_plain_words(monkeypatch):
    import pytest

    from shared.server_client import ServerClient, ServerError

    import http.client

    def refuse(*_a, **_k):
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(http.client.HTTPConnection, "connect", refuse)
    with pytest.raises(ServerError) as info:
        ServerClient("http://127.0.0.1:9").get("/health")
    assert "Cannot reach the server" in str(info.value) and "is the server running" in str(info.value)
    assert "Errno" not in str(info.value)


def test_one_connection_is_reused_and_reopened_after_the_server_drops_it(monkeypatch):
    """With https a new connection per request is a full handshake: the client keeps one open."""
    import http.client
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from shared.server_client import ServerClient

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, like uvicorn

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    connects = []
    real_connect = http.client.HTTPConnection.connect
    monkeypatch.setattr(http.client.HTTPConnection, "connect",
                        lambda self: (connects.append(1), real_connect(self))[1])
    try:
        client = ServerClient(f"http://127.0.0.1:{server.server_address[1]}")
        assert [client.get(f"/p{i}")["path"] for i in range(5)] == [f"/p{i}" for i in range(5)]
        assert len(connects) == 1
        client._local.conn.sock.close()  # the server's idle timeout closed it meanwhile
        assert client.get("/again")["path"] == "/again"
        assert len(connects) == 2
    finally:
        server.shutdown()
        server.server_close()
