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
