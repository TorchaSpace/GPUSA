"""Admin > Settings: the store's name/address, alert switches and data
location (admin_app/gui/components/store_settings_sections.py), then the
access side of the auth slice: who can sign in where, what happened at
sign-in.

- "Accounts & access": every sign-in account (an employee + role + PIN):
  badge, name, role, where that role signs in, status (Active / Switched
  off / Employee inactive / Locked until …), last sign-in. Actions on the
  selected account: Reset PIN, Change role, Switch off / on, Unlock,
  Remove; plus Add account. The last active administrator can't be
  demoted, switched off or removed (the repository refuses, the message
  is shown).
- "My account": you, and Change my PIN.
- "Sign-in activity": the latest 200 audit events (sign-ins, sign-outs,
  wrong PINs, lockouts, account changes) with where they happened.

The mockup's Settings page also carries "Safe Purchase Price Range" -
that already lives in Purchase requests (per product), so it isn't
duplicated here.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHeaderView, QInputDialog, QLabel, QMessageBox, QWidget

from admin_app.gui.components.account_form_popup import AddAccountPopup, PinPopup
from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.components.store_settings_sections import (
    DataLocationSection, GeneralSection, NotificationsSection,
)
from admin_app.gui.components.styled_table import cell, styled_table
from shared.i18n import LazyLabels, tr
from admin_app.theme import CLASSICAL_PALETTE
from database import account_repository
from database.exceptions import DATABASE_ERRORS
from shared import auth, current_session
from shared.formatting import local_datetime_text
from shared.models import Account

_WHERE = LazyLabels("admin.settings.where", ("admin", "depot_manager", "cashier"))
_EVENT_TEXT = LazyLabels(
    "admin.settings.event",
    ("sign_in", "sign_out", "failed", "locked", "refused", "pin_confirmed", "pin_changed", "account_created",
     "account_changed", "unlocked", "pin_reset", "recovery_code_created", "recovery_failed", "security_question_set"),
)


def account_status(account: Account) -> tuple[str, str]:
    p = CLASSICAL_PALETTE
    if account_repository.is_locked(account):
        return tr("admin.settings.locked_until").format(when=local_datetime_text(account.locked_until)), p["alert_critical"]
    if not account.employee_active:
        return tr("admin.settings.employee_inactive"), p["text_secondary"]
    if not account.is_active:
        return tr("admin.settings.switched_off"), p["text_secondary"]
    return tr("admin.settings.active"), p["alert_success"]


class SettingsPage(AdminPage):
    notifications_changed = Signal()  # MainWindow re-reads the alert switches

    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.settings.title"), parent, subtitle=tr("page.settings.subtitle"))
        p = CLASSICAL_PALETTE
        self._accounts: list[Account] = []

        refresh = CompactButton(tr("admin.refresh"))
        refresh.clicked.connect(self.reload)
        self.add_header_action(refresh)

        # Store-wide settings
        self._general = GeneralSection()
        self.body_layout().addWidget(self._general)
        self._notifications = NotificationsSection()
        self._notifications.changed.connect(self.notifications_changed)
        self.body_layout().addWidget(self._notifications)
        self._data_location = DataLocationSection()
        self.body_layout().addWidget(self._data_location)

        # My account
        self._me = Section(tr("admin.settings.me_kicker"), tr("admin.settings.me_heading"))
        self._me_label = QLabel()
        self._me_label.setStyleSheet(f"font-size: 13px; color: {p['text_primary']}; padding: 12px 16px;")
        self._me.body_layout().addWidget(self._me_label)
        change_pin = CompactButton(tr("admin.settings.change_pin"))
        change_pin.clicked.connect(self.change_my_pin)
        self._me.add_header_control(change_pin)
        question_button = CompactButton(tr("settings.security_question"))
        question_button.clicked.connect(self.set_security_question)
        self._me.add_header_control(question_button)
        recovery_button = CompactButton(tr("settings.recovery_code"))
        recovery_button.clicked.connect(self.make_recovery_code)
        self._me.add_header_control(recovery_button)
        self.body_layout().addWidget(self._me)

        # Accounts
        self._access = Section(tr("admin.settings.access_kicker"), tr("admin.settings.access_heading"))
        self._buttons = {}
        for key, label, handler in (
            ("add", tr("admin.settings.add_account"), self.add_account),
            ("pin", tr("admin.settings.reset_pin"), self.reset_pin),
            ("role", tr("admin.settings.change_role"), self.change_role),
            ("active", tr("admin.settings.switch_off"), self.toggle_active),
            ("unlock", tr("admin.settings.unlock"), self.unlock),
            ("remove", tr("admin.settings.remove"), self.remove),
        ):
            button = CompactButton(label)
            button.clicked.connect(handler)
            self._access.add_header_control(button)
            self._buttons[key] = button
        note = QLabel(
            tr("admin.settings.access_note").format(tries=auth.MAX_FAILED_ATTEMPTS, minutes=auth.LOCK_MINUTES)
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; padding: 10px 16px 0 16px;")
        self._access.body_layout().addWidget(note)
        self._table = styled_table(
            [tr(f"admin.settings.col_{key}") for key in ("badge", "name", "role", "signs_in", "status", "last")]
        )
        self._table.setMinimumHeight(240)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._access.body_layout().addWidget(self._table)
        self.body_layout().addWidget(self._access)

        # Activity
        self._activity = Section(tr("admin.settings.activity_kicker"), tr("admin.settings.activity_heading"))
        self._events = styled_table(
            [tr(f"admin.settings.ev_{key}") for key in ("time", "badge", "name", "event", "where", "detail")]
        )
        self._events.setMinimumHeight(260)
        self._events.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._events.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self._activity.body_layout().addWidget(self._events)
        self.body_layout().addWidget(self._activity)

        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Only when the page is (re)opened: reload() also runs after every
        # account action and must not wipe a half-typed store name - and
        # neither must coming back to the page: a section with unsaved
        # edits keeps them.
        if not self._general.is_dirty():
            self._general.reload()
        if not self._notifications.is_dirty():
            self._notifications.reload()
        self._data_location.reload()
        self.reload()

    # --- data -------------------------------------------------------------------

    def reload(self) -> None:
        session = current_session.get()
        self._me_label.setText(
            tr("admin.settings.me_line").format(
                name=session.name, badge=session.badge_id, role=session.role_label,
                when=local_datetime_text(session.signed_in_at), terminal=session.terminal,
            ) if session else tr("admin.settings.not_signed_in")
        )
        selected = self.selected()
        try:
            self._accounts = account_repository.list_accounts()
            events = account_repository.list_events(200)
        except DATABASE_ERRORS:
            return
        table = self._table
        table.setRowCount(len(self._accounts))
        for r, account in enumerate(self._accounts):
            status, color = account_status(account)
            for c, item in enumerate([
                cell(account.badge_id), cell(account.name), cell(auth.role_label(account.role)),
                cell(_WHERE.get(account.role, "")), cell(status, color=color),
                cell(local_datetime_text(account.last_sign_in_at) if account.last_sign_in_at else tr("admin.settings.never")),
            ]):
                table.setItem(r, c, item)
        if selected is not None:
            self.select(selected.badge_id)
        self._sync_buttons()

        self._events.setRowCount(len(events))
        for r, event in enumerate(events):
            where = " · ".join(part for part in (auth.AREA_NAMES.get(event["area"] or "", ""), event["terminal"] or "") if part)
            for c, value in enumerate([
                local_datetime_text(event["created_at"]), event["badge_id"] or "—", event["name"] or "—",
                _EVENT_TEXT.get(event["event"], event["event"]), where or "—", event["detail"] or "",
            ]):
                self._events.setItem(r, c, cell(value, color=CLASSICAL_PALETTE["alert_critical"]
                                                if c == 3 and event["event"] in ("failed", "locked") else None))

    def selected(self) -> Account | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows or rows[0].row() >= len(self._accounts):
            return None
        return self._accounts[rows[0].row()]

    def select(self, badge_id: str) -> None:
        for r, account in enumerate(self._accounts):
            if account.badge_id == badge_id:
                self._table.selectRow(r)
                return

    @staticmethod
    def _is_me(account: Account | None) -> bool:
        session = current_session.get()
        return account is not None and session is not None and (
            auth.normalize_badge_id(account.badge_id) == auth.normalize_badge_id(session.badge_id))

    def _refuse_self(self, account: Account | None) -> bool:
        """True (after telling the person) if `account` is the signed-in
        administrator: switching off, demoting or removing yourself is
        left to another administrator."""
        if not self._is_me(account):
            return False
        QMessageBox.information(self, tr("admin.settings.self_title"), tr("admin.settings.self_blocked"))
        return True

    def _sync_buttons(self) -> None:
        account = self.selected()
        for key in ("pin", "role", "active", "unlock", "remove"):
            self._buttons[key].setEnabled(account is not None)
        for key in ("role", "active", "remove"):
            mine = self._is_me(account)
            self._buttons[key].setEnabled(account is not None and not mine)
            self._buttons[key].setToolTip(tr("admin.settings.self_blocked") if mine else "")
        if account is not None:
            self._buttons["active"].setText(
                tr("admin.settings.switch_on") if not account.is_active else tr("admin.settings.switch_off")
            )
            self._buttons["unlock"].setEnabled(account_repository.is_locked(account) or account.failed_attempts > 0)

    def _run(self, fn, done: str | None = None) -> bool:
        try:
            fn()
        except (*DATABASE_ERRORS, ValueError) as exc:
            QMessageBox.warning(self, tr("admin.settings.save_failed"), str(exc))
            return False
        self.reload()
        return True

    # --- actions ----------------------------------------------------------------

    def add_account(self) -> None:
        try:
            employees = account_repository.employees_without_account()
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("admin.settings.load_employees_failed"), str(exc))
            return
        popup = AddAccountPopup(employees, self)
        if popup.exec():
            badge, role, pin = popup.values()
            if self._run(lambda: account_repository.create_account(badge, role, pin, by=current_session.actor())):
                self.select(badge)

    def reset_pin(self) -> None:
        account = self.selected()
        if account is None:
            return
        popup = PinPopup(account.name, account.role, ask_current=False, parent=self)
        if popup.exec():
            self._run(lambda: account_repository.set_pin(account.badge_id, popup.pin.text(), by=current_session.actor()))

    def change_role(self) -> None:
        account = self.selected()
        if account is None or self._refuse_self(account):
            return
        labels = [auth.role_label(role) for role in auth.ROLES]
        label, ok = QInputDialog.getItem(self, tr("admin.settings.change_role_title"),
                                         tr("admin.settings.role_for").format(name=account.name), labels,
                                         auth.ROLES.index(account.role), False)
        if not ok:
            return
        role = auth.ROLES[labels.index(label)]
        if role == account.role:
            return
        new_pin = None
        if role == "admin":
            # Promoting to administrator comes with a new, longer PIN in the
            # same step - the old till PIN must not become an admin key.
            QMessageBox.information(
                self, tr("admin.settings.promote_title"),
                tr("admin.settings.promote_info").format(name=account.name, n=auth.MIN_ADMIN_PIN_LENGTH))
            popup = PinPopup(account.name, "admin", ask_current=False, parent=self)
            if not popup.exec():
                return
            new_pin = popup.pin.text()
        self._run(lambda: account_repository.set_role(account.badge_id, role, by=current_session.actor(),
                                                      new_pin=new_pin))

    def toggle_active(self) -> None:
        account = self.selected()
        if account is not None and not (account.is_active and self._refuse_self(account)):
            self._run(lambda: account_repository.set_active(account.badge_id, not account.is_active,
                                                            by=current_session.actor()))

    def unlock(self) -> None:
        account = self.selected()
        if account is not None:
            self._run(lambda: account_repository.unlock(account.badge_id, by=current_session.actor()))

    def remove(self) -> None:
        account = self.selected()
        if account is None or self._refuse_self(account):
            return
        if QMessageBox.question(self, tr("admin.settings.remove_title"),
                                tr("admin.settings.remove_confirm").format(name=account.name)) != QMessageBox.Yes:
            return
        self._run(lambda: account_repository.delete(account.badge_id, by=current_session.actor()))

    def set_security_question(self) -> None:
        session = current_session.get()
        if session is None or session.role != "admin":
            return
        from admin_app.gui.auth_flow import SecurityQuestionDialog

        dialog = SecurityQuestionDialog(session, self)
        if dialog.exec() and dialog.saved:
            QMessageBox.information(self, tr("settings.security_question"), tr("settings.security_question_saved"))
            self.reload()

    def make_recovery_code(self) -> None:
        session = current_session.get()
        if session is None or session.role != "admin":
            return
        if account_repository.has_recovery_code() and QMessageBox.question(
            self, tr("settings.recovery_code"), tr("settings.recovery_code_confirm")
        ) != QMessageBox.Yes:
            return
        from admin_app.gui.auth_flow import show_new_recovery_code

        show_new_recovery_code(session, self)
        self.reload()

    def change_my_pin(self) -> None:
        session = current_session.get()
        if session is None:
            return
        popup = PinPopup(tr("admin.settings.you"), session.role, ask_current=True, parent=self)
        if popup.exec():
            if self._run(lambda: account_repository.change_own_pin(session, popup.current.text(), popup.pin.text())):
                QMessageBox.information(self, tr("admin.settings.pin_changed_title"), tr("admin.settings.pin_changed_body"))
