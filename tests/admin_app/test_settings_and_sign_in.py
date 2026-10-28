"""Offscreen tests: Admin's first-run / sign-in dialogs and Settings > accounts."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import account_repository, employee_repository
from shared import auth, current_session
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_first_run_dialog_creates_the_admin(qapp):
    from admin_app.gui.auth_flow import FirstAdminDialog

    dialog = FirstAdminDialog()
    dialog.name_input.setText("Erol Boz")
    dialog.badge_input.setText("A-1")
    dialog.pin_input.setText("482913")
    dialog.pin_again_input.setText("482914")
    dialog.create()
    assert "don't match" in dialog.error_label.text() and dialog.session is None
    dialog.pin_again_input.setText("482913")
    dialog.create()
    assert dialog.session is None and dialog.error_label.text()  # an answer is required too
    dialog.question_input.setEditText("Name of my first pet?")
    dialog.answer_input.setText("Pamuk")
    dialog.create()
    assert dialog.session.name == "Erol Boz" and account_repository.admin_exists()
    assert account_repository.get_security_question() == "Name of my first pet?"


def test_sign_in_dialog_shows_the_reason_and_keeps_the_badge(qapp):
    from admin_app.gui.auth_flow import admin_sign_in_dialog

    account_repository.create_first_admin("A-1", "Erol", "482913")
    dialog = admin_sign_in_dialog()
    dialog.badge_input.setText("A-1")
    dialog.pin_input.setText("111111")
    dialog.try_sign_in()
    assert dialog.error_label.text().startswith("Badge or PIN is wrong.")
    assert dialog.badge_input.text() == "A-1" and dialog.pin_input.text() == ""
    dialog.pin_input.setText("482913")
    dialog.try_sign_in()
    from PySide6.QtWidgets import QDialog

    assert dialog.session.badge_id == "A-1" and dialog.result() == QDialog.Accepted


@pytest.fixture
def settings(qapp):
    admin = account_repository.create_first_admin("A-1", "Erol", "482913")
    current_session.set(admin)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    from admin_app.gui.pages.settings_page import SettingsPage

    page = SettingsPage()
    page.show()
    pump(qapp)
    yield page
    page.close()


def _row_texts(table, row):
    return [table.item(row, c).text() for c in range(table.columnCount())]


def test_settings_lists_accounts_and_activity(settings):
    assert _row_texts(settings._table, 0)[:5] == ["A-1", "Erol", "Administrator", "Admin · depot Console · POS", "Active"]
    assert settings._events.item(0, 3).text() == "Signed in"
    assert "Erol · A-1 · Administrator" in settings._me_label.text()


def test_settings_account_actions(settings, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    account_repository.create_account("B-2", "cashier", "5831", by=current_session.actor())
    settings.reload()
    settings.select("B-2")
    settings.toggle_active()
    assert not account_repository.get("B-2").is_active
    assert "Switched off" in _row_texts(settings._table, [a.badge_id for a in settings._accounts].index("B-2"))
    settings.select("B-2")
    assert settings._buttons["active"].text() == "Switch on"

    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    settings.select("A-1")
    assert not settings._buttons["active"].isEnabled()  # you can't switch yourself off
    settings.toggle_active()
    assert infos and account_repository.get("A-1").is_active
    events = [settings._events.item(r, 3).text() for r in range(settings._events.rowCount())]
    assert "Account changed" in events and "Account created" in events


def test_locked_accounts_show_and_unlock(settings):
    account_repository.create_account("B-2", "cashier", "5831")
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        try:
            account_repository.authenticate("B-2", "0000", auth.AREA_POS, "POS")
        except Exception:
            pass
    settings.reload()
    row = [a.badge_id for a in settings._accounts].index("B-2")
    assert settings._table.item(row, 4).text().startswith("Locked until")
    settings.select("B-2")
    assert settings._buttons["unlock"].isEnabled()
    settings.unlock()
    assert not account_repository.is_locked(account_repository.get("B-2"))


def test_forgot_pin_with_the_recovery_code(qapp, tmp_path):
    from database import account_repository as accounts
    from shared import auth
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    session = accounts.create_first_admin("B-1", "Erol", "482913")
    code = accounts.create_recovery_code(session)
    dialog = ResetAdminAccessDialog()
    assert dialog.mode == "code" and dialog.admin_input.count() == 1
    dialog.code_input.setText("AAAA-BBBB-CCCC-DDDD")
    dialog.pin_input.setText("739184")
    dialog.pin_again_input.setText("739184")
    assert not dialog.save() and "not right" in dialog.message.text()
    dialog.code_input.setText(code)
    assert dialog.save() and dialog.reset_badge == "B-1"
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"


def test_forgot_pin_cycles_through_the_ways_back_in(qapp):
    from database import account_repository as accounts
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    session = accounts.create_first_admin("B-1", "Erol", "482913")
    accounts.set_security_question(session, "482913", "Name of my first pet?", "Pamuk")
    accounts.create_recovery_code(session)
    dialog = ResetAdminAccessDialog()
    seen = [dialog.mode]
    for _ in range(3):
        dialog.switch_mode()
        seen.append(dialog.mode)
    assert seen == ["question", "code", "reset", "question"]


def test_forgot_pin_with_nothing_set_up_offers_to_start_over(qapp, tmp_path):
    import database.connection as connection
    from database import account_repository as accounts, employee_repository
    from shared.models import Employee
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    accounts.create_first_admin("B-1", "Erol", "482913")
    employee_repository.create(Employee("B-2", "Selin", "Sales & service", "Dealership", "Harbor Point"))
    dialog = ResetAdminAccessDialog()
    assert dialog.mode == "reset" and not dialog.switch_button.isVisible()
    assert not dialog.erase()  # nothing happens until the word is typed
    assert accounts.admin_exists() and not dialog.erase_button.isEnabled()
    dialog.confirm_input.setText("yes")
    assert not dialog.erase_button.isEnabled() and not dialog.erase() and accounts.admin_exists()
    dialog.confirm_input.setText(" sil ")  # SIL or ERASE, any case
    assert dialog.erase_button.isEnabled()
    assert dialog.erase() and dialog.erased
    assert not accounts.admin_exists() and employee_repository.list_all() == []
    assert dialog.backup_path.exists()  # the old data is kept in a file beside the database


def test_forgot_pin_with_the_security_question(qapp, tmp_path):
    from database import account_repository as accounts
    from shared import auth
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    session = accounts.create_first_admin("B-1", "Erol", "482913")
    accounts.set_security_question(session, "482913", "Name of my first pet?", "Pamuk")
    dialog = ResetAdminAccessDialog()
    assert dialog.mode == "question" and dialog._code_caption.text() == "Name of my first pet?"
    dialog.code_input.setText("Karamel")
    dialog.pin_input.setText("739184")
    dialog.pin_again_input.setText("739184")
    assert not dialog.save() and "not right" in dialog.message.text()
    dialog.code_input.setText(" pamuk ")
    assert dialog.save() and dialog.reset_badge == "B-1"
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"


def test_settings_security_question_needs_the_current_pin(qapp):
    from database import account_repository as accounts
    from database.exceptions import AuthError

    session = accounts.create_first_admin("B-1", "Erol", "482913")
    try:
        accounts.set_security_question(session, "000000", "Name of my first pet?", "Pamuk")
        raise AssertionError("a wrong PIN must be refused")
    except AuthError:
        pass
    assert accounts.get_security_question() is None


def test_admin_left_alone_signs_out_and_asks_again(qapp, monkeypatch):
    from admin_app.gui.main_window import MainWindow

    account_repository.create_first_admin("A-1", "Erol", "482913")
    session = account_repository.authenticate("A-1", "482913", auth.AREA_ADMIN, "Admin")
    window = MainWindow(session)
    window.show()
    pump(qapp)
    signed_out = []
    monkeypatch.setattr(window, "sign_out", lambda: signed_out.append(True))
    window._idle_lock.last -= auth.IDLE_LOCK_SECONDS + 1
    assert window._idle_lock.check() and signed_out == [True]
    window._idle_lock.stop()
    window.close()


def test_a_session_ended_elsewhere_signs_the_window_out_on_the_next_refresh(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from admin_app.gui.main_window import MainWindow

    boss = account_repository.create_first_admin("A-1", "Erol", "482913")
    employee_repository.create(Employee("B-2", "Selin Kaya", "Management", "Dealership", "HQ"))
    account_repository.create_account("B-2", "admin", "246813")
    session = account_repository.authenticate("B-2", "246813", auth.AREA_ADMIN, "Admin")
    window = MainWindow(session)
    window._watcher.stop()
    window.show()
    pump(qapp)
    signed_out, warnings = [], []
    monkeypatch.setattr(window, "sign_out", lambda: signed_out.append(True))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    window.refresh_live()
    assert signed_out == []  # still a live administrator

    account_repository.set_role("B-2", "cashier", by=boss.actor)
    window.refresh_live()
    assert signed_out == [True] and len(warnings) == 1
    window._idle_lock.stop()
    window.close()


def test_a_locked_database_does_not_sign_anybody_out(qapp, monkeypatch):
    session = account_repository.create_first_admin("A-1", "Erol", "482913")

    def locked():
        raise __import__("sqlite3").OperationalError("database is locked")

    monkeypatch.setattr(account_repository, "connection_scope", locked)
    assert account_repository.is_session_valid(session) is False
    assert account_repository.is_session_valid(session, assume_on_error=True) is True


def _boot(monkeypatch, qapp, module):
    from PySide6.QtWidgets import QMessageBox

    from unittest import mock

    shown = []
    # A stand-in application: the real one is shared by the whole session and must not be re-themed.
    monkeypatch.setattr(module, "QApplication", lambda argv: mock.MagicMock())
    monkeypatch.setattr(module, "apply_theme", lambda *a, **k: None)
    if hasattr(module, "install_dialog_fade"):
        monkeypatch.setattr(module, "install_dialog_fade", lambda app: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: shown.append(args))
    monkeypatch.setattr(module, "daily_backup", lambda: None)
    return shown


@pytest.mark.parametrize("module_name", ["admin_app.main", "pos_app.main", "depot_app.main"])
def test_a_damaged_database_file_shows_a_dialog_instead_of_crashing(qapp, tmp_path, monkeypatch, module_name):
    import importlib

    module = importlib.import_module(module_name)
    (tmp_path / "t.db").write_bytes(b"this is not a sqlite database " * 50)
    shown = _boot(monkeypatch, qapp, module)

    assert module.main() == 1
    assert len(shown) == 1
    assert "t.db" in shown[0][2] and "backups" in shown[0][2]


def test_a_locked_database_at_sign_in_shows_the_dialog_too(qapp, monkeypatch):
    import sqlite3

    import admin_app.main as admin_main

    shown = _boot(monkeypatch, qapp, admin_main)

    def locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(admin_main, "sign_in", locked)
    assert admin_main.main() == 1
    assert len(shown) == 1 and "database is locked" in shown[0][2]
