"""Offscreen GUI tests for the Settings page's General / Notifications / Data location blocks."""

from __future__ import annotations

import pytest

from tests.gui_support import qapp  # noqa: F401

import database.connection as connection
from database import settings_repository
from shared import paths


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_general_saves_name_and_address(qapp):
    from admin_app.gui.components.store_settings_sections import GeneralSection

    section = GeneralSection()
    section._name_input.setText("Harbor Point")
    section._address_input.setPlainText("1 Quay St\nNorfolk")
    assert section.save()
    profile = settings_repository.load_store_profile()
    assert profile.name == "Harbor Point" and profile.address_lines == ("1 Quay St", "Norfolk")


def test_general_refuses_a_blank_name_and_says_why(qapp):
    from admin_app.gui.components.store_settings_sections import GeneralSection

    section = GeneralSection()
    section._name_input.setText("  ")
    assert not section.save()
    assert "needs a name" in section._error.text()


def test_notifications_save_and_emit(qapp):
    from admin_app.gui.components.store_settings_sections import NotificationsSection

    section = NotificationsSection()
    seen = []
    section.changed.connect(lambda: seen.append(1))
    section._low_stock.setChecked(False)
    assert section.save() and seen == [1]
    assert settings_repository.load_notifications().low_stock_alerts is False


def test_data_location_copies_and_repoints(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from admin_app.gui.components.store_settings_sections import DataLocationSection

    saved = []
    monkeypatch.setattr(paths, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(paths, "set_db_path", saved.append)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    settings_repository.save_store_profile(settings_repository.load_store_profile())  # create the file
    section = DataLocationSection()
    new_dir = tmp_path / "new"
    assert section.move_to(new_dir)
    assert (new_dir / "shared_backend.db").exists() and saved == [new_dir / "shared_backend.db"]


def test_language_choice_is_saved(qapp, monkeypatch):
    from admin_app.gui.components.store_settings_sections import GeneralSection

    asked = []
    monkeypatch.setattr(GeneralSection, "_confirm_restart", lambda self: asked.append(1) or False)
    section = GeneralSection()
    section._language_input.setCurrentIndex(section._language_input.findData("tr"))
    assert section.save()
    assert asked == [1]  # a language change offers a restart ("Later" keeps the window open)
    assert section._status.text()  # and says the save happened
    assert settings_repository.safe_language() == "tr"
    assert GeneralSection()._language_input.currentData() == "tr"


def test_a_moved_data_folder_applies_after_a_restart_not_to_the_running_app(qapp, tmp_path, monkeypatch):
    """Split brain guard: after "Change folder" the running Admin keeps using the pinned file (as the POS and
    Depot processes do) and only says where it will look next time."""
    import sqlite3

    from PySide6.QtWidgets import QMessageBox

    from admin_app.gui.components.store_settings_sections import DataLocationSection
    from database import product_repository
    from shared.models import Product

    old = tmp_path / "old" / "shared_backend.db"
    old.parent.mkdir()
    config = {"db_path": str(old)}
    monkeypatch.setattr(paths, "_load_config", lambda: dict(config))
    monkeypatch.setattr(paths, "_save_config", lambda c: config.update(c))
    monkeypatch.setattr(connection, "get_db_path", paths.get_db_path)  # the real lookup, not the test shortcut
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    connection.pin_db_path()  # what every app's main() does at start-up
    product_repository.create(Product("BEFORE", "x", 1, 0, 0))

    section = DataLocationSection()
    assert section._next_label.isHidden() and section._path_label.text() == str(old)
    new_folder = tmp_path / "new"
    new_folder.mkdir()
    assert section.move_to(new_folder)

    product_repository.create(Product("AFTER", "y", 1, 0, 0))  # still the OLD file, like the other apps
    in_old = {r[0] for r in sqlite3.connect(old).execute("SELECT barcode FROM products")}
    in_new = {r[0] for r in sqlite3.connect(new_folder / "shared_backend.db").execute("SELECT barcode FROM products")}
    assert "AFTER" in in_old and "AFTER" not in in_new
    assert section._path_label.text() == str(old)
    assert not section._next_label.isHidden()
    assert str(new_folder / "shared_backend.db") in section._next_label.text()

    connection._pinned = None  # the next start of the app
    product_repository.create(Product("RESTARTED", "z", 1, 0, 0))
    in_new = {r[0] for r in sqlite3.connect(new_folder / "shared_backend.db").execute("SELECT barcode FROM products")}
    assert "RESTARTED" in in_new


def test_once_a_currency_is_saved_not_chosen_is_no_longer_offered(qapp, monkeypatch):
    from admin_app.gui.components.store_settings_sections import GeneralSection
    from shared import currency

    monkeypatch.setattr(GeneralSection, "_confirm_restart", lambda self: False)
    monkeypatch.setattr(currency, "set_currency", lambda code: None)  # keep the process-wide setting untouched
    section = GeneralSection()
    section._name_input.setText("Magaza")
    assert section._currency_input.findData(None) >= 0  # nothing chosen yet: it is the starting point

    section._currency_input.setCurrentIndex(section._currency_input.findData("TRY"))
    assert section.save()
    section.reload()

    assert section._currency_input.findData(None) < 0
    assert section._currency_input.currentData() == "TRY"
    assert settings_repository.safe_currency() == "TRY"


def test_saving_without_a_currency_keeps_the_stored_one(qapp):
    profile = settings_repository.load_store_profile()
    settings_repository.save_general(profile, "en", "EUR")
    settings_repository.save_general(profile, "en", None)
    assert settings_repository.safe_currency() == "EUR"
