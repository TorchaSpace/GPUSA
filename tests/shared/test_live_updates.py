"""DataWatcher: another connection's commit raises `changed`; our own reads do not; bursts coalesce."""

from __future__ import annotations

import pytest

import database.connection as connection
from shared.gui_kit.live_updates import DataWatcher
from PySide6.QtTest import QTest

from tests.gui_support import qapp  # noqa: F401  (qapp is a fixture)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _write(text="x"):
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     ("k", text))


def test_a_commit_from_another_connection_is_noticed(qapp):
    watcher = DataWatcher(interval_ms=20, settle_ms=10, min_gap_ms=0)
    seen = []
    watcher.changed.connect(lambda: seen.append(1))
    watcher.start()
    QTest.qWait(60)
    assert seen == []  # starting is not a change, and reading is not a change
    _write("one")
    QTest.qWait(150)
    assert seen == [1]
    watcher.stop()


def test_a_burst_is_one_signal_and_idle_is_silent(qapp):
    watcher = DataWatcher(interval_ms=20, settle_ms=60, min_gap_ms=0)
    seen = []
    watcher.changed.connect(lambda: seen.append(1))
    watcher.start()
    for i in range(5):
        _write(str(i))
        watcher.check()
    QTest.qWait(250)
    assert seen == [1]
    QTest.qWait(150)
    assert seen == [1]
    watcher.stop()


def test_a_missing_database_does_not_raise(qapp, monkeypatch):
    watcher = DataWatcher(interval_ms=20, settle_ms=10, min_gap_ms=0)
    monkeypatch.setattr("shared.gui_kit.live_updates.get_connection", lambda: (_ for _ in ()).throw(OSError("gone")))
    watcher.start()
    assert watcher.check() is False
    watcher.stop()
