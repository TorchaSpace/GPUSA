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


class RecoveryCodeDialog(QDialog):
    """Shows a new recovery code ONCE, with the instruction to keep it
    somewhere safe (paper, in a drawer - not on this computer)."""

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.code = code
        self.setWindowTitle(tr("recovery.code_title"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        heading = QLabel(tr("recovery.code_title"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 24px; color: {p['text_primary']};")
        layout.addWidget(heading)
        body = QLabel(tr("recovery.code_body"))
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(body)
        self.code_label = QLabel(code)
        self.code_label.setAlignment(Qt.AlignCenter)
        self.code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 28px; color: {p['accent']}; padding: 14px;"
            f"border: 1px solid {p['border']}; border-radius: 6px;"
        )
        layout.addWidget(self.code_label)
        done = QPushButton(tr("recovery.code_saved"))
        done.setDefault(True)
        done.clicked.connect(self.accept)
        layout.addWidget(done, alignment=Qt.AlignRight)


class ResetAdminAccessDialog(QDialog):
    """"Forgot your PIN?". Two ways in, both safe:

    - the recovery code (default once one exists): type it, pick the
      administrator, set a new PIN. Guessing is rate-limited.
    - the proof file (when there is no code, or the code is lost):
      create RESET_ADMIN_ACCESS.txt in the data folder - something only a
      person with access to this computer's files can do.

    `reset_badge` holds who was reset, for the sign-in dialog to prefill."""

    def __init__(self, parent=None, flag_path: Path | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self._flag = Path(flag_path) if flag_path else recovery.recovery_flag_path(paths.get_db_path())
        self.reset_badge: str | None = None
        try:
            self._has_code = account_repository.has_recovery_code()
        except DATABASE_ERRORS:
            self._has_code = False
        self._mode = "code" if self._has_code else "file"
        self.setWindowTitle(tr("recovery.title"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        heading = QLabel(tr("recovery.title"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 24px; color: {p['text_primary']};")
        layout.addWidget(heading)
        self._intro = QLabel()
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(self._intro)

        # file mode, step 1: proof of file access
        self._file_host = QWidget()
        file_layout = QVBoxLayout(self._file_host)
        file_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_label = QLabel(str(self._flag.parent))
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.folder_label.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        file_layout.addWidget(self.folder_label)
        row = QHBoxLayout()
        open_button = QPushButton(tr("recovery.open_folder"))
        open_button.clicked.connect(self._open_folder)
        self.continue_button = QPushButton(tr("recovery.continue"))
        self.continue_button.clicked.connect(self.check_file)
        row.addWidget(open_button)
        row.addStretch(1)
        row.addWidget(self.continue_button)
        file_layout.addLayout(row)
        layout.addWidget(self._file_host)

        # the form: (code), administrator, new PIN twice
        self._form_host = QWidget()
        form_layout = QVBoxLayout(self._form_host)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self.code_input.setMaxLength(24)
        self.admin_input = QComboBox()
        self.pin_input = _pin_field()
        self.pin_again_input = _pin_field()
        self._code_caption = QLabel(tr("recovery.code"))
        form.addRow(self._code_caption, self.code_input)
        form.addRow(tr("recovery.admin"), self.admin_input)
        form.addRow(tr("recovery.new_pin").format(n=auth.MIN_ADMIN_PIN_LENGTH), self.pin_input)
        form.addRow(tr("recovery.pin_again"), self.pin_again_input)
        form_layout.addLayout(form)
        self.save_button = QPushButton(tr("recovery.set_pin"))
        self.save_button.clicked.connect(self.save)
        form_layout.addWidget(self.save_button)
        layout.addWidget(self._form_host)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.message.hide()
        layout.addWidget(self.message)

        bottom = QHBoxLayout()
        self.switch_button = QPushButton()
        self.switch_button.setFlat(True)
        self.switch_button.setCursor(Qt.PointingHandCursor)
        self.switch_button.setStyleSheet(f"QPushButton {{ color: {p['accent']}; border: none; background: transparent; }}")
        self.switch_button.clicked.connect(self.switch_mode)
        bottom.addWidget(self.switch_button)
        bottom.addStretch(1)
        close = QPushButton(tr("common.close"))
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self._show_mode()

    # --- modes ---------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def _show_mode(self) -> None:
        self._say("")
        if self._mode == "code":
            self._intro.setText(tr("recovery.code_intro"))
            self._file_host.hide()
            self._code_caption.show()
            self.code_input.show()
            if not self._load_admins(show_badge=False):
                self._form_host.hide()
            else:
                self._form_host.show()
            self.switch_button.setText(tr("recovery.no_code"))
        else:
            self._intro.setText(tr("recovery.step1").format(name=recovery.RECOVERY_FILENAME))
            self._file_host.show()
            self._form_host.hide()
            self._code_caption.hide()
            self.code_input.hide()
            self.switch_button.setText(tr("recovery.have_code"))
        self.switch_button.setVisible(self._has_code or self._mode == "code")

    def switch_mode(self) -> None:
        self._mode = "file" if self._mode == "code" else "code"
        self._show_mode()

    def _load_admins(self, show_badge: bool) -> bool:
        try:
            admins = account_repository.list_active_admins()
        except DATABASE_ERRORS as exc:
            self._say(str(exc))
            return False
        if not admins:
            self._say(tr("recovery.no_admins"))
            return False
        self.admin_input.clear()
        for account in admins:
            label = f"{account.name} ({account.badge_id})" if show_badge else account.name
            self.admin_input.addItem(label, account.badge_id)
        return True

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._flag.parent)))

    # --- file mode -------------------------------------------------------------

    def check_file(self) -> bool:
        if not recovery.is_armed(self._flag):
            self._say(tr("recovery.not_found").format(name=recovery.RECOVERY_FILENAME))
            return False
        if not self._load_admins(show_badge=True):
            return False
        self._say("")
        self._intro.setText(tr("recovery.step2"))
        self._file_host.hide()
        self._form_host.show()
        return True

    # --- both modes ------------------------------------------------------------

    def save(self) -> bool:
        if self.pin_input.text() != self.pin_again_input.text():
            self._say(tr("recovery.mismatch"))
            return False
        badge = self.admin_input.currentData()
        try:
            if self._mode == "code":
                account_repository.reset_with_recovery_code(
                    self.code_input.text(), badge, self.pin_input.text(), terminal_name())
            else:
                if not recovery.is_armed(self._flag):  # re-checked: the proof must still be there
                    self._say(tr("recovery.not_found").format(name=recovery.RECOVERY_FILENAME))
                    return False
                account_repository.reset_admin_access(badge, self.pin_input.text(), terminal_name())
                recovery.disarm(self._flag)
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._say(str(exc))
            return False
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


def show_new_recovery_code(session: Session, parent=None) -> str | None:
    """Create a recovery code and show it once. Returns it (None if it
    couldn't be made - Settings > My account can make one later)."""
    try:
        code = account_repository.create_recovery_code(session)
    except DATABASE_ERRORS:
        return None
    RecoveryCodeDialog(code, parent).exec()
    return code


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
    accepted = dialog.exec() == QDialog.Accepted
    if accepted and isinstance(dialog, FirstAdminDialog) and dialog.session is not None:
        show_new_recovery_code(dialog.session, parent)
    return dialog.session if accepted else None
