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
        assert answer["gpusa"] == 1 and answer["port"] == 8123 and set(answer) == {"gpusa", "port", "version"}
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
