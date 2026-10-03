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


def test_language_choice_is_saved(qapp):
    from admin_app.gui.components.store_settings_sections import GeneralSection

    section = GeneralSection()
    section._language_input.setCurrentIndex(section._language_input.findData("tr"))
    assert section.save()
    assert settings_repository.safe_language() == "tr"
    assert GeneralSection()._language_input.currentData() == "tr"
