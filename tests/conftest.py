"""Shared pytest fixtures.

`db_connection` builds a fresh temp-file SQLite database from
database/schema.sql for every test that needs one, and cleans it up
afterward. No test should ever import database.connection.get_connection()
directly and touch the real shared_backend.db - that file is meant to
simulate a live, shared backend, and a test accidentally writing to it
could corrupt real stock counts.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from database.connection import _SCHEMA_PATH  # reuse the same schema source of truth
from database.migrations import run_migrations


@pytest.fixture
def db_connection():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_backend.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        run_migrations(conn)
        try:
            yield conn
        finally:
            conn.close()


@pytest.fixture(autouse=True)
def _fast_pin_hashing(monkeypatch):
    """PIN hashing is deliberately slow (shared/auth.PBKDF2_ITERATIONS);
    tests don't need that, so every test hashes with far fewer rounds.
    test_auth.py checks the real default separately."""
    from shared import auth

    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1_000)


@pytest.fixture(autouse=True)
def _database_not_pinned():
    """database.connection pins the file per process once an app's main() ran;
    a test that boots main() must not leave the next test on its file."""
    import database.connection as connection

    connection._pinned = None
    yield
    connection._pinned = None


@pytest.fixture(autouse=True)
def _nobody_signed_in():
    """shared.current_session is process-wide; don't let one test's
    sign-in leak into the next."""
    from shared import current_session

    current_session.clear()
    yield
    current_session.clear()



@pytest.fixture(autouse=True)
def _server_mode_off():
    """database.remote is process-wide; a test that switches server mode on
    must not send the next test's repository calls to a server."""
    from database import remote

    remote.drop_standing()
    remote.use_server(None)
    yield
    remote.drop_standing()
    remote.use_server(None)


@pytest.fixture(autouse=True)
def _no_server_address(monkeypatch):
    """No test signs in to a real server or writes the real config.json's
    server address: GPUSA_SERVER_URL is unset and the saved address lives in
    memory (tests/shared/test_server_address.py tests the real functions)."""
    from shared import paths

    monkeypatch.delenv(paths.SERVER_URL_ENV, raising=False)
    saved: dict = {}
    monkeypatch.setattr(paths, "get_server_url", lambda: saved.get("url"))
    monkeypatch.setattr(paths, "set_server_url", lambda url: saved.__setitem__("url", (url or "").strip() or None))
    monkeypatch.setattr(paths, "get_server_pairing", lambda: saved.get("pairing"))
    monkeypatch.setattr(paths, "set_server_pairing", lambda pairing: saved.__setitem__("pairing", pairing))
    monkeypatch.setattr(paths, "get_server_cert", lambda: saved.get("cert"))
    monkeypatch.setattr(paths, "set_server_cert", lambda pem: saved.__setitem__("cert", pem or None))
    monkeypatch.setattr(paths, "get_admin_sign_in_kind", lambda: saved.get("kind"))
    monkeypatch.setattr(paths, "set_admin_sign_in_kind", lambda kind: saved.__setitem__("kind", kind))


@pytest.fixture(autouse=True)
def _no_network_discovery(monkeypatch):
    """The apps look for the server on the network when they start; a test run must never adopt one."""
    monkeypatch.setenv("GPUSA_DISCOVERY", "0")
