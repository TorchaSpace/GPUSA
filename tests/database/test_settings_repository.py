"""database.settings_repository and connection.copy_database_to."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import product_repository, settings_repository
from shared import store_settings as ss
from shared.models import Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def test_unset_settings_are_the_defaults():
    assert settings_repository.load_store_profile() == ss.StoreProfile()
    assert settings_repository.load_notifications() == ss.NotificationPrefs()


def test_profile_and_prefs_persist_and_overwrite():
    settings_repository.save_store_profile(ss.StoreProfile("Harbor", ("1 Quay St",)))
    settings_repository.save_store_profile(ss.StoreProfile("Harbor Point", ("2 Quay St", "Norfolk")))
    settings_repository.save_notifications(ss.NotificationPrefs(low_stock_alerts=False, pending_approvals=True))
    assert settings_repository.load_store_profile() == ss.StoreProfile("Harbor Point", ("2 Quay St", "Norfolk"))
    assert settings_repository.load_notifications() == ss.NotificationPrefs(False, True)


def test_copy_database_keeps_the_data(tmp_path):
    product_repository.create(Product("BOX", "Carton", 40, 5, 1))
    settings_repository.save_store_profile(ss.StoreProfile("Copy Me"))
    target = tmp_path / "elsewhere" / "shared_backend.db"
    connection.copy_database_to(target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT name FROM products").fetchone()[0] == "Carton"
        assert conn.execute("SELECT value FROM app_settings WHERE key = 'store.name'").fetchone()[0] == "Copy Me"
    with pytest.raises(FileExistsError):
        connection.copy_database_to(target)
