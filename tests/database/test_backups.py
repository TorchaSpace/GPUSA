"""database.backups: one copy a day beside the database, the newest
DAILY_BACKUPS_KEPT kept, never an error that stops an app opening."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

import database.connection as connection
from database import backups, product_repository
from shared.models import Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "shared_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def test_the_first_call_of_the_day_writes_a_readable_copy(tmp_path):
    product_repository.create(Product("OIL", "Oil", 10.0, 5, 1))
    path = backups.daily_backup(date(2026, 10, 8))
    assert path == tmp_path / "backups" / "shared_backend-2026-10-08.db"
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT name FROM products").fetchall() == [("Oil",)]
    copy.close()


def test_a_second_call_the_same_day_keeps_the_first_copy(tmp_path):
    product_repository.create(Product("OIL", "Oil", 10.0, 5, 1))
    first = backups.daily_backup(date(2026, 10, 8))
    product_repository.create(Product("RICE", "Rice", 4.0, 5, 1))
    assert backups.daily_backup(date(2026, 10, 8)) == first
    copy = sqlite3.connect(first)
    assert copy.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1
    copy.close()
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_only_the_newest_copies_are_kept(tmp_path):
    start = date(2026, 10, 1)
    for offset in range(5):
        backups.daily_backup(start + timedelta(days=offset), keep=3)
    names = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert names == [f"shared_backend-2026-10-0{d}.db" for d in (3, 4, 5)]


def test_other_files_in_the_backup_folder_are_left_alone(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "my-notes.txt").write_text("keep me")
    for offset in range(3):
        backups.daily_backup(date(2026, 10, 1) + timedelta(days=offset), keep=1)
    assert (folder / "my-notes.txt").exists()


def test_a_failure_is_swallowed(monkeypatch):
    def boom(_destination):
        raise OSError("disk full")

    monkeypatch.setattr(backups, "copy_database_to", boom)
    assert backups.daily_backup(date(2026, 10, 8)) is None


def test_pin_db_path_keeps_the_process_on_one_file_and_backups_follow(tmp_path, monkeypatch):
    first, second = tmp_path / "first" / "shared_backend.db", tmp_path / "second" / "shared_backend.db"
    first.parent.mkdir()
    second.parent.mkdir()
    where = {"path": first}
    monkeypatch.setattr(connection, "get_db_path", lambda: where["path"])
    assert connection.pinned_db_path() is None

    assert connection.pin_db_path() == first
    where["path"] = second  # the setting changes while the app runs
    assert connection.active_db_path() == first
    with connection.connection_scope():
        pass
    assert first.exists() and not second.exists()
    assert backups.daily_backup(date(2026, 10, 8)) == first.parent / "backups" / "shared_backend-2026-10-08.db"
    assert not (second.parent / "backups").exists()


def test_future_dated_copies_from_a_wrong_clock_are_left_alone_and_today_survives(tmp_path):
    future = [date(2027, 1, 1) + timedelta(days=i) for i in range(14)]
    for day in future:
        assert backups.daily_backup(day).exists()
    past = [date(2026, 9, 1) + timedelta(days=i) for i in range(5)]
    for day in past:
        backups.daily_backup(day, keep=100)

    path = backups.daily_backup(date(2026, 10, 28), keep=3)  # the clock has been corrected

    assert path == tmp_path / "backups" / "shared_backend-2026-10-28.db" and path.exists()
    names = sorted(p.name for p in (tmp_path / "backups").glob("shared_backend-*.db"))
    assert all(f"shared_backend-{day:%Y-%m-%d}.db" in names for day in future)  # nothing from the "future" lost
    kept_past = [n for n in names if n < "shared_backend-2027"]
    assert kept_past == ["shared_backend-2026-09-04.db", "shared_backend-2026-09-05.db", "shared_backend-2026-10-28.db"]
