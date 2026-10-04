"""Offscreen tests: Settings page guards (self-service, promotion PIN,
unsaved edits), the one-transaction General save, the typed erase word, and
sign-in dialog details."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import account_repository, employee_repository, settings_repository
from shared import auth, current_session
from shared.i18n import tr
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    monkeypatch.setattr("shared.paths.get_db_path", lambda: tmp_path / "t.db")


@pytest.fixture
def settings(qapp):
    admin = account_repository.create_first_admin("A-1", "Erol", "482913")
    current_session.set(admin)
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    account_repository.create_account("B-2", "cashier", "1357")
    from admin_app.gui.pages.settings_page import SettingsPage

    page = SettingsPage()
    page.show()
    pump(qapp)
    yield page
    page.close()
    current_session.clear()


@pytest.fixture
def silent_boxes(monkeypatch):
    """Record QMessageBox.information/warning instead of blocking on them."""
    from PySide6.QtWidgets import QMessageBox

    seen = {"information": [], "warning": []}
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: seen["information"].append(a[1:]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen["warning"].append(a[1:]))
    return seen


# --- self-service guards -----------------------------------------------------------------

def test_own_row_cannot_be_demoted_switched_off_or_removed(settings, silent_boxes):
    settings.select("A-1")
    for key in ("role", "active", "remove"):
        assert not settings._buttons[key].isEnabled()
    for action in (settings.change_role, settings.toggle_active, settings.remove):
        action()
    assert len(silent_boxes["information"]) == 3  # told why, three times
    account = account_repository.get("A-1")
    assert account.role == "admin" and account.is_active
    settings.select("B-2")
    for key in ("role", "active", "remove"):
        assert settings._buttons[key].isEnabled()


# --- promotion to administrator ----------------------------------------------------------

def _patch_role_choice(monkeypatch, label):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: (label, True))


def _patch_pin_popup(monkeypatch, pin, accept=True):
    from admin_app.gui.components.account_form_popup import PinPopup

    def fake_exec(self):
        self.pin.setText(pin)
        self.pin_again.setText(pin)
        return 1 if accept else 0

    monkeypatch.setattr(PinPopup, "exec", fake_exec)


def test_promoting_to_admin_asks_for_a_new_pin_and_sets_it(settings, silent_boxes, monkeypatch):
    settings.select("B-2")
    _patch_role_choice(monkeypatch, auth.role_label("admin"))
    _patch_pin_popup(monkeypatch, "739184")
    settings.change_role()
    assert account_repository.get("B-2").role == "admin"
    assert account_repository.authenticate("B-2", "739184", auth.AREA_ADMIN, "Admin").role == "admin"
    with pytest.raises(Exception):  # the old cashier PIN is no key to Admin
        account_repository.authenticate("B-2", "1357", auth.AREA_ADMIN, "Admin")


def test_cancelling_the_promotion_pin_changes_nothing(settings, silent_boxes, monkeypatch):
    settings.select("B-2")
    _patch_role_choice(monkeypatch, auth.role_label("admin"))
    _patch_pin_popup(monkeypatch, "739184", accept=False)
    settings.change_role()
    assert account_repository.get("B-2").role == "cashier"
    assert account_repository.authenticate("B-2", "1357", auth.AREA_POS, "POS").role == "cashier"


def test_a_weak_promotion_pin_is_refused_by_the_popup_itself(qapp):
    from admin_app.gui.components.account_form_popup import PinPopup

    popup = PinPopup("Selin", "admin", ask_current=False)
    popup.pin.setText("1357")
    popup.pin_again.setText("1357")
    popup._try_accept()
    assert popup.result() == 0 and popup.error.text()  # still open, with the reason


# --- unsaved edits survive coming back to the page ---------------------------------------

def test_returning_to_settings_keeps_unsaved_edits(settings, qapp):
    settings._general._name_input.setText("Half typed")
    settings._notifications._low_stock.setChecked(False)
    assert settings._general.is_dirty() and settings._notifications.is_dirty()
    settings.hide()
    settings.show()
    pump(qapp)
    assert settings._general._name_input.text() == "Half typed"
    assert not settings._notifications._low_stock.isChecked()


def test_returning_to_settings_refreshes_untouched_sections(settings, qapp):
    assert not settings._general.is_dirty()
    settings_repository.save_store_profile(settings_repository.ss.StoreProfile("Changed Elsewhere", ()))
    settings.hide()
    settings.show()
    pump(qapp)
    assert settings._general._name_input.text() == "Changed Elsewhere"


def test_saving_clears_the_dirty_flag(settings):
    settings._general._name_input.setText("Acme Parts")
    assert settings._general.is_dirty()
    assert settings._general.save()
    assert not settings._general.is_dirty()


# --- General save is one transaction --------------------------------------------------------

def test_general_save_writes_profile_and_language_in_one_call(settings, monkeypatch):
    calls = []
    real = settings_repository.save_profile_and_language
    monkeypatch.setattr(settings_repository, "save_profile_and_language",
                        lambda profile, language: (calls.append((profile.name, language)), real(profile, language))[1])
    forbidden = lambda *a, **k: pytest.fail("profile and language must not be saved separately")  # noqa: E731
    monkeypatch.setattr(settings_repository, "save_store_profile", forbidden)
    monkeypatch.setattr(settings_repository, "save_language", forbidden)
    general = settings._general
    monkeypatch.setattr(type(general), "_confirm_restart", lambda self: False)
    general._name_input.setText("Acme Parts")
    general._language_input.setCurrentIndex(general._language_input.findData("tr"))
    assert general.save()
    assert calls == [("Acme Parts", "tr")]
    assert settings_repository.safe_language() == "tr" and settings_repository.load_store_profile().name == "Acme Parts"


def test_invalid_general_input_saves_neither_name_nor_language(settings):
    general = settings._general
    general._name_input.setText("   ")
    general._language_input.setCurrentIndex(general._language_input.findData("tr"))
    assert not general.save() and general._error.text()
    assert settings_repository.safe_language() == "en"


# --- sign-in dialogs ------------------------------------------------------------------------

def test_sign_in_dialog_normalises_the_badge_and_ignores_a_second_submit(qapp):
    from admin_app.theme import CLASSICAL_PALETTE
    from shared.gui_kit.sign_in_dialog import SignInDialog

    seen = []

    def authenticate(badge, pin):
        seen.append(badge)
        raise ValueError("Badge or PIN is wrong.")

    dialog = SignInDialog("Sign in", "x", authenticate, CLASSICAL_PALETTE)
    dialog.badge_input.setText("  a-1 ")
    dialog.pin_input.setText("482913")
    dialog.try_sign_in()
    assert seen == ["A-1"] and dialog.error_label.text() == "Badge or PIN is wrong."
    assert dialog.sign_in_button.isEnabled()  # usable again after a failure


def test_start_over_needs_the_word_either_case_and_says_everything_is_erased(qapp):
    from admin_app.gui.auth_flow import ResetAdminAccessDialog

    account_repository.create_first_admin("A-1", "Erol", "482913")
    dialog = ResetAdminAccessDialog()
    assert dialog.mode == "reset"
    assert tr("admin.recovery.erase_warning") == dialog._erase_warning.text()
    assert "SIL" in dialog._erase_prompt.text() or tr("admin.recovery.erase_type") == dialog._erase_prompt.text()
    assert not dialog.erase_button.isEnabled()
    dialog.erase_button.click()  # a click alone does nothing
    assert account_repository.admin_exists() and not dialog.erased
    for wrong in ("", "no", "evet", "sil sil"):
        dialog.confirm_input.setText(wrong)
        assert not dialog.erase_button.isEnabled() and not dialog.erase()
    assert account_repository.admin_exists()
    dialog.confirm_input.setText("erase")
    assert dialog.erase_button.isEnabled() and dialog.erase()
    assert dialog.erased and dialog.backup_path.exists() and not account_repository.admin_exists()


def test_first_admin_dialog_checks_the_answer_before_creating_anything(qapp):
    from admin_app.gui.auth_flow import FirstAdminDialog

    dialog = FirstAdminDialog()
    dialog.name_input.setText("Erol")
    dialog.badge_input.setText("a-1")
    dialog.pin_input.setText("482913")
    dialog.pin_again_input.setText("482913")
    dialog.question_input.setEditText("Name of my first pet?")
    dialog.answer_input.setText("abc")  # 3 letters: too short for an answer
    dialog.create()
    assert dialog.session is None and dialog.error_label.text()
    assert not account_repository.admin_exists()  # no half-created administrator
    dialog.answer_input.setText("Pamuk")
    dialog.create()
    assert dialog.session.badge_id == "A-1" and account_repository.admin_exists()


# --- POS till ----------------------------------------------------------------------------------

def test_a_switched_off_dealership_closes_its_till(qapp):
    from database import dealership_repository
    from pos_app.gui.auth_flow import till_dealership_problem, till_sign_in_dialog
    from shared.models import Dealership

    account_repository.create_first_admin("A-1", "Erol", "482913")
    employee_repository.create(Employee("B-2", "Selin", "Sales & service", "Dealership", "Harbor Point"))
    account_repository.create_account("B-2", "cashier", "5831")
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))

    assert till_dealership_problem("001") is None and till_dealership_problem(None) is None
    assert till_dealership_problem("999") is None  # not registered (yet): don't block a dev till
    dialog = till_sign_in_dialog("001", "Harbor Point")
    dialog.badge_input.setText("B-2")
    dialog.pin_input.setText("5831")
    dialog.try_sign_in()
    assert dialog.session is not None

    off = dealership_repository.get_by_code("001")
    off.is_active = False
    dealership_repository.update(off)
    assert till_dealership_problem("001")
    dialog = till_sign_in_dialog("001", "Harbor Point")
    dialog.badge_input.setText("B-2")
    dialog.pin_input.setText("5831")
    dialog.try_sign_in()
    assert dialog.session is None and "switched off" in dialog.error_label.text()
    off.is_active = True
    dealership_repository.update(off)
    assert till_dealership_problem("001") is None
