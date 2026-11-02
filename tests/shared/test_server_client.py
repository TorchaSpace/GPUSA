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
    import urllib.error
    import urllib.request

    import pytest

    from shared.server_client import ServerClient, ServerError

    def refuse(*_a, **_k):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(ServerError) as info:
        ServerClient("http://127.0.0.1:9").get("/health")
    assert "Cannot reach the server" in str(info.value) and "is the server running" in str(info.value)
    assert "Errno" not in str(info.value)
