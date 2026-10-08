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
def _nobody_signed_in():
    """shared.current_session is process-wide; don't let one test's
    sign-in leak into the next."""
    from shared import current_session

    current_session.clear()
    yield
    current_session.clear()


# PySide can crash while the interpreter tears its Qt objects down AFTER
# every test has run and been reported (exit 139 / bus error on CI). The
# run's real result is known by then, so leave straight away with it
# instead of letting interpreter shutdown turn a green run red.
_exit_status = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_status
    _exit_status = int(exitstatus)


@pytest.hookimpl(tryfirst=True)
def pytest_unconfigure(config):
    import os
    import sys

    if os.environ.get("GPUSA_HARD_EXIT", "1") != "1":
        return
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)
