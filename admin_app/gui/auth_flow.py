"""Admin's sign-in: the one-time "create the first administrator" screen
on a fresh system, otherwise the badge + PIN sign-in (shared
SignInDialog, Classical-styled). See shared/auth.py for the rules.
"""

from __future__ import annotations

import socket

from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import account_repository
from database.exceptions import DataAccessError
from shared import auth
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


def admin_sign_in_dialog(parent=None, cancel_text: str = "Quit") -> SignInDialog:
    return SignInDialog(
        "Sign in to Admin",
        "Administrators only. Use your badge and PIN.",
        lambda badge, pin: account_repository.authenticate(badge, pin, auth.AREA_ADMIN, terminal_name()),
        palette={**CLASSICAL_PALETTE, "on_accent": "#161514"},
        cancel_text=cancel_text,
        parent=parent,
    )


def sign_in(parent=None) -> Session | None:
    """Run the right dialog and return the Session, or None if cancelled.
    Raises DataAccessError if the database can't be read at all."""
    if not account_repository.admin_exists():
        dialog = FirstAdminDialog(parent)
    else:
        dialog = admin_sign_in_dialog(parent)
    return dialog.session if dialog.exec() == QDialog.Accepted else None
