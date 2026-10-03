"""Admin's sign-in: the one-time "create the first administrator" screen
on a fresh system, otherwise the badge + PIN sign-in (shared
SignInDialog, Classical-styled). See shared/auth.py for the rules.
"""

from __future__ import annotations

import socket
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QWidget, QFormLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
)

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import account_repository
from database.exceptions import DATABASE_ERRORS, DataAccessError
from shared import auth, paths, recovery
from shared.i18n import tr
from shared.auth import Session
from shared.gui_kit.sign_in_dialog import SignInDialog


def terminal_name() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    return f"Admin · {host}" if host else "Admin"


def _pin_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setMaxLength(auth.MAX_PIN_LENGTH)
    return field


class FirstAdminDialog(QDialog):
    """Shown only while no active administrator exists. Creates the
    employee (or uses an existing badge), the admin account, and signs in."""

    def __init__(self, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.session: Session | None = None
        self.setWindowTitle("Create the first administrator")
        self.setMinimumWidth(440)
        self.setObjectName("firstAdmin")
        self.setStyleSheet(
            f"#firstAdmin {{ background-color: {p['background']}; }} QLabel {{ background: transparent; border: none; }}"
            f"QLineEdit {{ background-color: {p['surface']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
            f"border-radius: {p['radius_sm']}; padding: 7px 9px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel("Welcome to GPUSA")
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 26px; color: {p['text_primary']};")
        layout.addWidget(heading)
        intro = QLabel(
            "Nobody can sign in yet. Create the first administrator - you'll use this badge and PIN to open "
            "Admin, and can add everyone else in Settings. Use a badge that's already in Workforce, or a new one."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(intro)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.badge_input = QLineEdit()
        self.pin_input = _pin_field()
        self.pin_again_input = _pin_field()
        form.addRow("Your name", self.name_input)
        form.addRow("Badge ID", self.badge_input)
        form.addRow(f"PIN ({auth.MIN_ADMIN_PIN_LENGTH}+ digits)", self.pin_input)
        form.addRow("PIN again", self.pin_again_input)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.reject)
        create = QPushButton("Create and sign in")
        create.setDefault(True)
        create.setStyleSheet(f"background-color: {p['accent']}; color: #161514; border: none; padding: 7px 14px;")
        create.clicked.connect(self.create)
        buttons.addWidget(quit_button)
        buttons.addWidget(create)
        layout.addLayout(buttons)

    def create(self) -> None:
        if self.pin_input.text() != self.pin_again_input.text():
            self._fail("The two PINs don't match.")
            return
        try:
            self.session = account_repository.create_first_admin(
                self.badge_input.text(), self.name_input.text(), self.pin_input.text(), terminal_name()
            )
        except (ValueError, DataAccessError) as exc:
            self._fail(str(exc))
            return
        self.accept()

    def _fail(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()


class ResetAdminAccessDialog(QDialog):
    """"Forgot your PIN?": proof of file access first (shared/recovery.py),
    then pick an administrator and set a new PIN. `reset_badge` holds who
    was reset, for the sign-in dialog to prefill."""

    def __init__(self, parent=None, flag_path: Path | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self._flag = Path(flag_path) if flag_path else recovery.recovery_flag_path(paths.get_db_path())
        self.reset_badge: str | None = None
        self.setWindowTitle(tr("recovery.title"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        heading = QLabel(tr("recovery.title"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 24px; color: {p['text_primary']};")
        layout.addWidget(heading)

        # step 1 - proof of file access
        self._step1 = QVBoxLayout()
        intro = QLabel(tr("recovery.step1").format(name=recovery.RECOVERY_FILENAME))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        self._step1.addWidget(intro)
        self.folder_label = QLabel(str(self._flag.parent))
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folder_label.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        self._step1.addWidget(self.folder_label)
        row = QHBoxLayout()
        open_button = QPushButton(tr("recovery.open_folder"))
        open_button.clicked.connect(self._open_folder)
        self.continue_button = QPushButton(tr("recovery.continue"))
        self.continue_button.clicked.connect(self.check_file)
        row.addWidget(open_button)
        row.addStretch(1)
        row.addWidget(self.continue_button)
        self._step1.addLayout(row)
        self._step1_host = QWidget()
        self._step1_host.setLayout(self._step1)
        layout.addWidget(self._step1_host)

        # step 2 - new PIN
        self._step2_host = QWidget()
        step2 = QVBoxLayout(self._step2_host)
        step2.setContentsMargins(0, 0, 0, 0)
        note = QLabel(tr("recovery.step2"))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        step2.addWidget(note)
        form = QFormLayout()
        self.admin_input = QComboBox()
        self.pin_input = _pin_field()
        self.pin_again_input = _pin_field()
        form.addRow(tr("recovery.admin"), self.admin_input)
        form.addRow(tr("recovery.new_pin").format(n=auth.MIN_ADMIN_PIN_LENGTH), self.pin_input)
        form.addRow(tr("recovery.pin_again"), self.pin_again_input)
        step2.addLayout(form)
        self.save_button = QPushButton(tr("recovery.set_pin"))
        self.save_button.clicked.connect(self.save)
        step2.addWidget(self.save_button)
        self._step2_host.hide()
        layout.addWidget(self._step2_host)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.message.hide()
        layout.addWidget(self.message)
        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.reject)
        layout.addWidget(close, alignment=Qt.AlignRight)

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._flag.parent)))

    def check_file(self) -> bool:
        if not recovery.is_armed(self._flag):
            self._say(tr("recovery.not_found").format(name=recovery.RECOVERY_FILENAME))
            return False
        try:
            admins = account_repository.list_active_admins()
        except DATABASE_ERRORS as exc:
            self._say(str(exc))
            return False
        if not admins:
            self._say(tr("recovery.no_admins"))
            return False
        self._say("")
        self.admin_input.clear()
        for account in admins:
            self.admin_input.addItem(f"{account.name} ({account.badge_id})", account.badge_id)
        self._step1_host.hide()
        self._step2_host.show()
        return True

    def save(self) -> bool:
        if not recovery.is_armed(self._flag):  # re-checked: the proof must still be there
            self._say(tr("recovery.not_found").format(name=recovery.RECOVERY_FILENAME))
            return False
        if self.pin_input.text() != self.pin_again_input.text():
            self._say(tr("recovery.mismatch"))
            return False
        badge = self.admin_input.currentData()
        try:
            account_repository.reset_admin_access(badge, self.pin_input.text(), terminal_name())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._say(str(exc))
            return False
        recovery.disarm(self._flag)
        self.reset_badge = badge
        self.accept()
        return True


def _offer_recovery(dialog: SignInDialog) -> None:
    reset = ResetAdminAccessDialog(dialog)
    if reset.exec() == QDialog.Accepted and reset.reset_badge:
        dialog.badge_input.setText(reset.reset_badge)
        dialog.pin_input.clear()
        dialog.pin_input.setFocus()
        dialog.error_label.setText(tr("recovery.done"))
        dialog.error_label.show()


def admin_sign_in_dialog(parent=None, cancel_text: str = "Quit") -> SignInDialog:
    return SignInDialog(
        "Sign in to Admin",
        "Administrators only. Use your badge and PIN.",
        lambda badge, pin: account_repository.authenticate(badge, pin, auth.AREA_ADMIN, terminal_name()),
        palette={**CLASSICAL_PALETTE, "on_accent": "#161514"},
        cancel_text=cancel_text,
        parent=parent,
        extra_action=(tr("signin.forgot"), _offer_recovery),
    )


def sign_in(parent=None) -> Session | None:
    """Run the right dialog and return the Session, or None if cancelled.
    Raises DataAccessError if the database can't be read at all."""
    if not account_repository.admin_exists():
        dialog = FirstAdminDialog(parent)
    else:
        dialog = admin_sign_in_dialog(parent)
    return dialog.session if dialog.exec() == QDialog.Accepted else None
