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
from admin_app.theme import CLASSICAL_PALETTE
from database import account_repository
from database.exceptions import DataAccessError
from shared import auth, current_session
from shared.formatting import local_datetime_text
from shared.models import Account

_WHERE = {
    "admin": "Admin · depot Console · POS",
    "depot_manager": "depot Console & Portal",
    "cashier": "POS tills",
}
_EVENT_TEXT = {
    "sign_in": "Signed in", "sign_out": "Signed out", "failed": "Failed sign-in", "locked": "Locked out",
    "refused": "Refused", "pin_confirmed": "Portal unlocked", "pin_changed": "PIN changed",
    "account_created": "Account created", "account_changed": "Account changed", "unlocked": "Unlocked",
}


def account_status(account: Account) -> tuple[str, str]:
    p = CLASSICAL_PALETTE
    if account_repository.is_locked(account):
        return f"Locked until {local_datetime_text(account.locked_until)}", p["alert_critical"]
    if not account.employee_active:
        return "Employee inactive", p["text_secondary"]
    if not account.is_active:
        return "Switched off", p["text_secondary"]
    return "Active", p["alert_success"]


class SettingsPage(AdminPage):
    notifications_changed = Signal()  # MainWindow re-reads the alert switches

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Settings", parent)
        p = CLASSICAL_PALETTE
        self._accounts: list[Account] = []

        refresh = CompactButton("Refresh")
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
        self._me = Section("My account", "Signed in")
        self._me_label = QLabel()
        self._me_label.setStyleSheet(f"font-size: 13px; color: {p['text_primary']}; padding: 12px 16px;")
        self._me.body_layout().addWidget(self._me_label)
        change_pin = CompactButton("Change my PIN")
        change_pin.clicked.connect(self.change_my_pin)
        self._me.add_header_control(change_pin)
        self.body_layout().addWidget(self._me)

        # Accounts
        self._access = Section("Accounts & access", "Who can sign in")
        self._buttons = {}
        for key, label, handler in (
            ("add", "Add account", self.add_account), ("pin", "Reset PIN", self.reset_pin),
            ("role", "Change role", self.change_role), ("active", "Switch off", self.toggle_active),
            ("unlock", "Unlock", self.unlock), ("remove", "Remove", self.remove),
        ):
            button = CompactButton(label)
            button.clicked.connect(handler)
            self._access.add_header_control(button)
            self._buttons[key] = button
        note = QLabel(
            "An account is an employee from Workforce with a role and a PIN. Administrators sign in to Admin "
            "(and can open the depot Console and a till); depot managers open the depot's Manager Console; "
            f"cashiers sign in at a till. {auth.MAX_FAILED_ATTEMPTS} wrong PINs in a row lock an account for "
            f"{auth.LOCK_MINUTES} minutes."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; padding: 10px 16px 0 16px;")
        self._access.body_layout().addWidget(note)
        self._table = styled_table(["Badge", "Name", "Role", "Signs in at", "Status", "Last sign-in"])
        self._table.setMinimumHeight(240)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.itemSelectionChanged.connect(self._sync_buttons)
        self._access.body_layout().addWidget(self._table)
        self.body_layout().addWidget(self._access)

        # Activity
        self._activity = Section("Sign-in activity", "Latest events")
        self._events = styled_table(["Time", "Badge", "Name", "Event", "Where", "Detail"])
        self._events.setMinimumHeight(260)
        self._events.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._events.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self._activity.body_layout().addWidget(self._events)
        self.body_layout().addWidget(self._activity)

        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()

    # --- data -------------------------------------------------------------------

    def reload(self) -> None:
        self._general.reload()
        self._notifications.reload()
        self._data_location.reload()
        session = current_session.get()
        self._me_label.setText(
            f"{session.name} · {session.badge_id} · {session.role_label} · signed in at "
            f"{local_datetime_text(session.signed_in_at)} on {session.terminal}" if session else "Not signed in."
        )
        selected = self.selected()
        try:
            self._accounts = account_repository.list_accounts()
            events = account_repository.list_events(200)
        except DataAccessError:
            return
        table = self._table
        table.setRowCount(len(self._accounts))
        for r, account in enumerate(self._accounts):
            status, color = account_status(account)
            for c, item in enumerate([
                cell(account.badge_id), cell(account.name), cell(auth.role_label(account.role)),
                cell(_WHERE.get(account.role, "")), cell(status, color=color),
                cell(local_datetime_text(account.last_sign_in_at) if account.last_sign_in_at else "never"),
            ]):
                table.setItem(r, c, item)
        if selected is not None:
            self.select(selected.badge_id)
        self._sync_buttons()

        self._events.setRowCount(len(events))
        for r, event in enumerate(events):
            where = " · ".join(part for part in (auth.AREA_LABELS.get(event["area"] or "", ""), event["terminal"] or "") if part)
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

    def _sync_buttons(self) -> None:
        account = self.selected()
        for key in ("pin", "role", "active", "unlock", "remove"):
            self._buttons[key].setEnabled(account is not None)
        if account is not None:
            self._buttons["active"].setText("Switch on" if not account.is_active else "Switch off")
            self._buttons["unlock"].setEnabled(account_repository.is_locked(account) or account.failed_attempts > 0)

    def _run(self, fn, done: str | None = None) -> bool:
        try:
            fn()
        except (DataAccessError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return False
        self.reload()
        return True

    # --- actions ----------------------------------------------------------------

    def add_account(self) -> None:
        try:
            employees = account_repository.employees_without_account()
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't load employees", str(exc))
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
        if account is None:
            return
        labels = [auth.role_label(role) for role in auth.ROLES]
        label, ok = QInputDialog.getItem(self, "Change role", f"Role for {account.name}:", labels,
                                         auth.ROLES.index(account.role), False)
        if not ok:
            return
        role = auth.ROLES[labels.index(label)]
        if role == "admin" and account.role != "admin":
            QMessageBox.information(self, "Administrator PIN",
                                    f"Administrators need a PIN of at least {auth.MIN_ADMIN_PIN_LENGTH} digits - "
                                    "reset this person's PIN next if theirs is shorter.")
        self._run(lambda: account_repository.set_role(account.badge_id, role, by=current_session.actor()))

    def toggle_active(self) -> None:
        account = self.selected()
        if account is not None:
            self._run(lambda: account_repository.set_active(account.badge_id, not account.is_active,
                                                            by=current_session.actor()))

    def unlock(self) -> None:
        account = self.selected()
        if account is not None:
            self._run(lambda: account_repository.unlock(account.badge_id, by=current_session.actor()))

    def remove(self) -> None:
        account = self.selected()
        if account is None:
            return
        if QMessageBox.question(self, "Remove account?",
                                f"Remove {account.name}'s sign-in account? They stay in Workforce.") != QMessageBox.Yes:
            return
        self._run(lambda: account_repository.delete(account.badge_id, by=current_session.actor()))

    def change_my_pin(self) -> None:
        session = current_session.get()
        if session is None:
            return
        popup = PinPopup("you", session.role, ask_current=True, parent=self)
        if popup.exec():
            if self._run(lambda: account_repository.change_own_pin(session, popup.current.text(), popup.pin.text())):
                QMessageBox.information(self, "PIN changed", "Your new PIN works from your next sign-in.")
