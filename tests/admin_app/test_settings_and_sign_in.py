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
    assert dialog.session.name == "Erol Boz" and account_repository.admin_exists()


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

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    settings.select("A-1")
    settings.toggle_active()  # the only administrator
    assert warnings and "no active administrator" in warnings[0]
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


def test_forgot_pin_needs_the_proof_file_then_resets(qapp, tmp_path):
    from database import account_repository as accounts
    from shared import auth, recovery
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    accounts.create_first_admin("B-1", "Erol", "482913")
    flag = tmp_path / recovery.RECOVERY_FILENAME
    dialog = ResetAdminAccessDialog(flag_path=flag)
    assert not dialog.check_file()  # no file yet: stays on step 1
    assert "isn't in that folder" in dialog.message.text()
    flag.write_text("")
    assert dialog.check_file() and dialog.admin_input.count() == 1
    dialog.pin_input.setText("739184")
    dialog.pin_again_input.setText("739185")
    assert not dialog.save() and "match" in dialog.message.text()
    dialog.pin_again_input.setText("739184")
    assert dialog.save() and dialog.reset_badge == "B-1"
    assert not flag.exists()  # one use only
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"


def test_forgot_pin_with_the_recovery_code(qapp, tmp_path):
    from database import account_repository as accounts
    from shared import auth
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    session = accounts.create_first_admin("B-1", "Erol", "482913")
    code = accounts.create_recovery_code(session)
    dialog = ResetAdminAccessDialog(flag_path=tmp_path / "RESET_ADMIN_ACCESS.txt")
    assert dialog.mode == "code" and dialog.admin_input.count() == 1
    dialog.code_input.setText("AAAA-BBBB-CCCC-DDDD")
    dialog.pin_input.setText("739184")
    dialog.pin_again_input.setText("739184")
    assert not dialog.save() and "not right" in dialog.message.text()
    dialog.code_input.setText(code)
    assert dialog.save() and dialog.reset_badge == "B-1"
    assert accounts.authenticate("B-1", "739184", auth.AREA_ADMIN, "t").badge_id == "B-1"


def test_forgot_pin_can_switch_to_the_file_way_when_a_code_exists(qapp, tmp_path):
    from database import account_repository as accounts
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    accounts.create_recovery_code(accounts.create_first_admin("B-1", "Erol", "482913"))
    dialog = ResetAdminAccessDialog(flag_path=tmp_path / "RESET_ADMIN_ACCESS.txt")
    dialog.switch_mode()
    assert dialog.mode == "file"
    dialog.switch_mode()
    assert dialog.mode == "code"
