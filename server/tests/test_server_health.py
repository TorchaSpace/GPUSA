import os

import pytest
from fastapi.testclient import TestClient

from gpusa_server import db
from gpusa_server.config import Settings
from gpusa_server.main import create_app


def test_health_is_ok_without_a_database():
    client = TestClient(create_app(Settings()))
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_db_reports_not_configured():
    client = TestClient(create_app(Settings()))
    r = client.get("/health/db")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "database": "not_configured"}


def test_health_db_ok_and_unreachable(monkeypatch):
    client = TestClient(create_app(Settings(database_url="postgresql://u:p@h/d")))

    monkeypatch.setattr(db, "ping", lambda settings: None)
    assert client.get("/health/db").json() == {"status": "ok", "database": "ok"}

    def boom(settings):
        raise OSError("connection refused for user u password p")

    monkeypatch.setattr(db, "ping", boom)
    r = client.get("/health/db")
    assert r.status_code == 503
    assert r.json() == {"status": "error", "database": "unreachable"}


@pytest.mark.skipif(not os.environ.get("GPUSA_TEST_DATABASE_URL"),
                    reason="set GPUSA_TEST_DATABASE_URL to test against a real PostgreSQL")
def test_health_db_against_real_postgres():
    settings = Settings(database_url=os.environ["GPUSA_TEST_DATABASE_URL"])
    r = TestClient(create_app(settings)).get("/health/db")
    assert r.status_code == 200, r.json()


def test_network_discovery_answers_the_question_and_only_that():
    """New PCs on the network ask 'GPUSA-DISCOVER?' by UDP broadcast; the answer names the API port, nothing else."""
    import json
    import socket

    from gpusa_server import discovery

    responder = discovery.Responder(port=0, http_port=8123, host="127.0.0.1").start()
    try:
        ask = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ask.settimeout(2)
        ask.sendto(discovery.QUESTION, ("127.0.0.1", responder.port))
        answer = json.loads(ask.recvfrom(512)[0])
        assert answer["gpusa"] == 1 and answer["port"] == 8123 and set(answer) == {"gpusa", "port", "version", "tls"}
        ask.sendto(b"something else", ("127.0.0.1", responder.port))
        ask.settimeout(0.5)
        with pytest.raises(socket.timeout):
            ask.recvfrom(512)
        ask.close()
    finally:
        responder.stop()


def test_discovery_can_be_switched_off(monkeypatch):
    from gpusa_server import discovery

    monkeypatch.setenv("GPUSA_DISCOVERY", "0")
    assert discovery.start_from_environment() is None


def test_the_discovery_reply_says_whether_the_api_speaks_https(monkeypatch):
    import json

    from gpusa_server import discovery

    monkeypatch.delenv("GPUSA_TLS", raising=False)
    assert json.loads(discovery.reply(8000))["tls"] == 0  # plain uvicorn: http
    monkeypatch.setenv("GPUSA_TLS", "1")  # set by python -m gpusa_server.run
    assert json.loads(discovery.reply(8000))["tls"] == 1


def test_the_server_makes_its_own_certificate_once(tmp_path):
    import os
    import ssl

    pytest.importorskip("cryptography")
    from gpusa_server import tls

    cert, key, made = tls.ensure_certificate(tmp_path / "tls")
    assert made and cert.is_file() and key.is_file()
    assert (os.stat(key).st_mode & 0o077) == 0  # the key is the owner's alone
    fingerprint = tls.fingerprint(cert)
    assert len(fingerprint.split(":")) == 32
    # kept: a new one on every start would make every computer type the address again
    assert tls.ensure_certificate(tmp_path / "tls") == (cert, key, False) and tls.fingerprint(cert) == fingerprint
    # usable as it is for https, and as the one certificate a computer trusts (shared/tls.py)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cadata=cert.read_text())
    client.check_hostname = False


def test_the_runner_starts_uvicorn_with_the_certificate(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    import uvicorn

    from gpusa_server import run

    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw, app=app))
    monkeypatch.setenv("GPUSA_TLS_DIR", str(tmp_path / "tls"))
    monkeypatch.delenv("GPUSA_TLS", raising=False)
    monkeypatch.delenv("GPUSA_PORT", raising=False)
    run.main([])
    assert started["app"] == "gpusa_server.main:app" and started["port"] == 8000 and started["host"] == "0.0.0.0"
    assert started["ssl_certfile"].endswith("cert.pem") and started["ssl_keyfile"].endswith("key.pem")
    assert os.environ["GPUSA_TLS"] == "1"
    started.clear()
    monkeypatch.setenv("GPUSA_TLS", "0")
    run.main(["--port", "9000"])
    assert started["port"] == 9000 and "ssl_certfile" not in started and os.environ["GPUSA_TLS"] == "0"
