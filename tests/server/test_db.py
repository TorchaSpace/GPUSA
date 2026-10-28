import sys
import types

from server import db
from server.config import load_settings

IAM = load_settings({"GPUSA_DB_HOST": "db.example", "GPUSA_DB_REGION": "eu-north-1", "GPUSA_DB_USER": "gpusa"})


def test_iam_kwargs_use_the_token_as_password():
    kw = db.connect_kwargs(IAM, token_factory=lambda s: "TOKEN")
    assert kw["password"] == "TOKEN"
    assert (kw["host"], kw["user"], kw["sslmode"]) == ("db.example", "gpusa", "require")


def test_password_mode_never_asks_for_a_token():
    s = load_settings({"GPUSA_DB_HOST": "h", "GPUSA_DB_AUTH": "password", "GPUSA_DB_PASSWORD": "pw"})

    def boom(_):
        raise AssertionError("no token needed")

    assert db.connect_kwargs(s, token_factory=boom)["password"] == "pw"


def test_database_url_passthrough():
    s = load_settings({"DATABASE_URL": "postgresql://u@localhost/x"})
    assert db.connect_kwargs(s) == {"conninfo": "postgresql://u@localhost/x"}


def test_iam_token_is_cached(monkeypatch):
    calls = []

    class FakeClient:
        def generate_db_auth_token(self, **kw):
            calls.append(kw)
            return f"tok{len(calls)}"

    fake = types.SimpleNamespace(client=lambda service, region_name: FakeClient())
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setattr(db, "_token_cache", {})
    assert db.iam_token(IAM) == db.iam_token(IAM) == "tok1"
    assert len(calls) == 1
    assert calls[0]["DBHostname"] == "db.example" and calls[0]["Region"] == "eu-north-1"
