"""The database and its backups are readable by their owner only (POSIX)."""

from __future__ import annotations

import os
import stat
from datetime import date

import pytest

import database.connection as connection
from database import backups

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(backups, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_the_database_file_is_owner_only(tmp_path):
    with connection.connection_scope():
        pass
    assert _mode(tmp_path / "t.db") == 0o600


def test_backups_and_their_folder_are_owner_only(tmp_path):
    with connection.connection_scope():
        pass
    target = backups.daily_backup(date(2026, 10, 8))
    assert target is not None and _mode(target) == 0o600 and _mode(target.parent) == 0o700
