"""Admin's sign-in: the one-time "create the first administrator" screen
on a fresh system, otherwise the badge + PIN sign-in (shared
SignInDialog, Classical-styled). See shared/auth.py for the rules.
"""

from __future__ import annotations

import socket
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QWidget, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QHBoxLayout, QVBoxLayout,
)

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import account_repository
from database.connection import ERASE_CONFIRM_WORDS, erase_all_data, erase_confirmed
from database.exceptions import DATABASE_ERRORS, DataAccessError
from shared import auth, paths, security_question
from shared.i18n import tr
from shared.auth import Session
from shared.gui_kit.server_line import ServerLine
from shared.gui_kit.sign_in_dialog import SignInDialog

ROOT_BADGE = "B-001"  # offered on the first-run screen; the person may type another


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
        self.server_changed = False
        self.setWindowTitle(tr("admin.auth.first_title"))
        self.setMinimumWidth(440)
        self.setObjectName("firstAdmin")
        self.setStyleSheet(
            f"#firstAdmin {{ background-color: {p['background']}; }} QLabel {{ background: transparent; border: none; }}"
            f"QLineEdit {{ background-color: {p['surface']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
            f"border-radius: {p['radius_sm']}; padding: 7px 9px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        heading = QLabel(tr("admin.auth.welcome"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 26px; color: {p['text_primary']};")
        layout.addWidget(heading)
        intro = QLabel(tr("admin.auth.first_intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(intro)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.badge_input = QLineEdit(ROOT_BADGE)
        self.pin_input = _pin_field()
        self.pin_again_input = _pin_field()
        self.setup_code_input = QLineEdit()
        self.setup_code_input.setPlaceholderText(tr("admin.auth.setup_code_hint"))
        try:
            self._needs_code = account_repository.needs_setup_code()
        except DATABASE_ERRORS:
            self._needs_code = False
        if self._needs_code:
            form.addRow(tr("admin.auth.setup_code"), self.setup_code_input)
        form.addRow(tr("admin.auth.your_name"), self.name_input)
        form.addRow(tr("admin.auth.badge"), self.badge_input)
        form.addRow(tr("admin.auth.pin_label").format(n=auth.MIN_ADMIN_PIN_LENGTH), self.pin_input)
        form.addRow(tr("admin.auth.pin_again"), self.pin_again_input)
        # The way back in if the PIN is ever forgotten ("Forgot your PIN?").
        self.question_input = QComboBox()
        self.question_input.setEditable(True)
        for key in security_question.PRESET_KEYS:
            self.question_input.addItem(tr(key))
        self.question_input.setCurrentIndex(0)
        self.answer_input = QLineEdit()
        form.addRow(tr("question.label"), self.question_input)
        form.addRow(tr("question.answer"), self.answer_input)
        layout.addLayout(form)
        hint = QLabel(tr("question.first_admin_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(hint)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # Where root is made: on the server this PC found (or the one typed here), else on this PC alone.
        self.server_line = ServerLine(p, self)
        self.server_line.changed.connect(self._server_changed)
        layout.addWidget(self.server_line)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        quit_button = QPushButton(tr("admin.auth.quit"))
        quit_button.clicked.connect(self.reject)
        create = QPushButton(tr("admin.auth.create"))
        create.setDefault(True)
        create.setStyleSheet(f"background-color: {p['accent']}; color: #161514; border: none; padding: 7px 14px;")
        create.clicked.connect(self.create)
        buttons.addWidget(quit_button)
        buttons.addWidget(create)
        layout.addLayout(buttons)

    def _server_changed(self, _url: str) -> None:
        """Another database now: it may have its root already (then sign in), or ask for a setup code.
        sign_in() looks again and shows the right screen."""
        self.server_changed = True
        self.reject()

    def create(self) -> None:
        if self.pin_input.text() != self.pin_again_input.text():
            self._fail(tr("admin.auth.mismatch"))
            return
        try:
            # Checked first, so a bad answer doesn't leave an admin without a way back in.
            account_repository.validate_security_answer(self.question_input.currentText(), self.answer_input.text())
            self.session = account_repository.create_first_admin(
                self.badge_input.text(), self.name_input.text(), self.pin_input.text(), terminal_name(),
                self.setup_code_input.text() if self._needs_code else "",
            )
            account_repository.set_security_question(
                self.session, self.pin_input.text(), self.question_input.currentText(), self.answer_input.text()
            )
        except (ValueError, DataAccessError) as exc:
            self._fail(str(exc))
            return
        self.accept()

    def _fail(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()


class SecurityQuestionDialog(QDialog):
    """Settings > My account > Security question: choose (or write) a
    question, answer it, and confirm with the current PIN."""

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self._session = session
        self.saved = False
        self.setWindowTitle(tr("settings.security_question_title"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        heading = QLabel(tr("settings.security_question_title"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 24px; color: {p['text_primary']};")
        layout.addWidget(heading)
        form = QFormLayout()
        self.question_input = QComboBox()
        self.question_input.setEditable(True)
        for key in security_question.PRESET_KEYS:
            self.question_input.addItem(tr(key))
        current = account_repository.get_security_question()
        self.question_input.setEditText(current or tr(security_question.PRESET_KEYS[0]))
        self.answer_input = QLineEdit()
        self.pin_input = _pin_field()
        form.addRow(tr("question.label"), self.question_input)
        form.addRow(tr("question.answer"), self.answer_input)
        form.addRow(tr("settings.security_question_pin"), self.pin_input)
        layout.addLayout(form)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.message.hide()
        layout.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("common.save"))
        save.setDefault(True)
        save.clicked.connect(self.save)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)

    def save(self) -> bool:
        try:
            account_repository.set_security_question(
                self._session, self.pin_input.text(), self.question_input.currentText(), self.answer_input.text())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.message.setText(str(exc))
            self.message.show()
            return False
        self.saved = True
        self.accept()
        return True


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
    """"Forgot your PIN or badge?" - three ways back in, easiest first:

    - the security question: answer it, pick the administrator, set a new PIN;
    - the recovery code, if one was made (Settings > My account);
    - start over: when nothing else is possible, the data is backed up to a
      file beside the database and erased, and a new administrator is
      created. It is guarded by a typed confirmation word (SIL / ERASE) and
      says plainly that everything will be erased. Nothing is exposed by
      this - it can only destroy, and it keeps a copy.

    `reset_badge` holds who was reset (for the sign-in dialog to prefill);
    `erased` is True after a start-over."""

    def __init__(self, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.reset_badge: str | None = None
        self.erased = False
        self.backup_path: Path | None = None
        try:
            self._has_code = account_repository.has_recovery_code()
            self._question = account_repository.get_security_question()
        except DATABASE_ERRORS:
            self._has_code, self._question = False, None
        # Ways back in, most everyday first; "Try another way" cycles through them.
        # On a server installation the data is on the server: "start over" would only empty this PC's cache (and
        # any sales still waiting to be sent), never fix the sign-in, so it is replaced by how to set a new PIN
        # on the server PC.
        on_server = bool(paths.get_server_url())
        self._modes = [m for m, on in (("question", bool(self._question)), ("code", self._has_code),
                                       ("server_help", on_server), ("reset", not on_server)) if on]
        self._mode = self._modes[0]
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

        # the form: (question / code), administrator, new PIN twice
        self._form_host = QWidget()
        form_layout = QVBoxLayout(self._form_host)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.code_input = QLineEdit()
        self.admin_input = QComboBox()
        self.pin_input = _pin_field()
        self.pin_again_input = _pin_field()
        self._code_caption = QLabel()
        self._code_caption.setWordWrap(True)
        form.addRow(self._code_caption, self.code_input)
        # On a server installation the list may be empty (a fresh PC has no local accounts), and someone who
        # forgot the badge can type their exact name: so the box is editable there.
        self.admin_input.setEditable(on_server)
        self.new_badge_input = QLineEdit()
        self.new_badge_input.setMaxLength(32)
        self.new_badge_input.setPlaceholderText(tr("recovery.new_badge_hint"))
        form.addRow(tr("recovery.admin_or_badge") if on_server else tr("recovery.admin"), self.admin_input)
        if on_server:
            form.addRow(tr("recovery.new_badge"), self.new_badge_input)
        form.addRow(tr("recovery.new_pin").format(n=auth.MIN_ADMIN_PIN_LENGTH), self.pin_input)
        form.addRow(tr("recovery.pin_again"), self.pin_again_input)
        form_layout.addLayout(form)
        self.save_button = QPushButton(tr("recovery.set_pin"))
        self.save_button.clicked.connect(self.save)
        form_layout.addWidget(self.save_button)
        layout.addWidget(self._form_host)

        # start over
        self._reset_host = QWidget()
        reset_layout = QVBoxLayout(self._reset_host)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        self._erase_warning = QLabel(tr("admin.recovery.erase_warning"))
        self._erase_warning.setWordWrap(True)
        self._erase_warning.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {p['alert_critical']};")
        reset_layout.addWidget(self._erase_warning)
        self._erase_prompt = QLabel(tr("admin.recovery.erase_type").format(words=" / ".join(ERASE_CONFIRM_WORDS)))
        self._erase_prompt.setWordWrap(True)
        reset_layout.addWidget(self._erase_prompt)
        # No one-click wipe at a sign-in screen: the word has to be typed.
        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText(ERASE_CONFIRM_WORDS[0])
        self.confirm_input.textChanged.connect(lambda text: self.erase_button.setEnabled(erase_confirmed(text)))
        reset_layout.addWidget(self.confirm_input)
        self.erase_button = QPushButton(tr("recovery.erase"))
        self.erase_button.setEnabled(False)
        self.erase_button.setStyleSheet(f"QPushButton {{ color: {p['alert_critical']}; border: 1px solid {p['alert_critical']}; }}")
        self.erase_button.clicked.connect(self.erase)
        reset_layout.addWidget(self.erase_button)
        layout.addWidget(self._reset_host)

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
        self._reset_host.setVisible(self._mode == "reset")
        if self._mode == "reset":
            self._intro.setText(tr("recovery.erase_intro"))
            self._form_host.hide()
        elif self._mode == "server_help":
            self._intro.setText(tr("recovery.server_help"))
            self._intro.setTextInteractionFlags(Qt.TextSelectableByMouse)  # the command can be copied
            self._form_host.hide()
        else:
            self.code_input.clear()
            if self._mode == "question":
                self._intro.setText(tr("recovery.question_intro"))
                self._code_caption.setText(self._question or "")
                self.code_input.setPlaceholderText(tr("question.answer"))
                self.code_input.setMaxLength(120)
            else:
                self._intro.setText(tr("recovery.code_intro"))
                self._code_caption.setText(tr("recovery.code"))
                self.code_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
                self.code_input.setMaxLength(24)
            self._form_host.setVisible(self._load_admins())
        self.switch_button.setText(tr("recovery.another_way"))
        self.switch_button.setVisible(len(self._modes) > 1)

    def switch_mode(self) -> None:
        self._mode = self._modes[(self._modes.index(self._mode) + 1) % len(self._modes)]
        self._show_mode()

    def _load_admins(self) -> bool:
        """Names only: the badge is exactly what a person who forgot it
        cannot give, and it is filled in for them afterwards."""
        try:
            admins = account_repository.list_active_admins()
        except DATABASE_ERRORS as exc:
            self._say(str(exc))
            return False
        if not admins:
            if paths.get_server_url():
                self.admin_input.clear()
                return True  # type the badge or the name
            self._say(tr("recovery.no_admins"))
            return False
        self.admin_input.clear()
        for account in admins:
            self.admin_input.addItem(account.name, account.badge_id)
        return True

    def _say(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    # --- actions ---------------------------------------------------------------

    def save(self) -> bool:
        if self.pin_input.text() != self.pin_again_input.text():
            self._say(tr("recovery.mismatch"))
            return False
        badge = self.admin_input.currentData() or self.admin_input.currentText().strip()
        new_badge = self.new_badge_input.text().strip() or None
        try:
            if self._mode == "question":
                signed_badge = account_repository.reset_with_security_answer(
                    self.code_input.text(), badge, self.pin_input.text(), terminal_name(), new_badge)
            else:
                signed_badge = account_repository.reset_with_recovery_code(
                    self.code_input.text(), badge, self.pin_input.text(), terminal_name(), new_badge)
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._say(str(exc))
            return False
        self.reset_badge = signed_badge or badge
        self.accept()
        return True

    def erase(self) -> bool:
        """Back everything up, then wipe it, so a new administrator can be
        created. Only after the confirmation word has been typed (SIL or
        ERASE, any letter case)."""
        if not erase_confirmed(self.confirm_input.text()):
            self._say(tr("admin.recovery.erase_type").format(words=" / ".join(ERASE_CONFIRM_WORDS)))
            return False
        try:
            self.backup_path = erase_all_data(self.confirm_input.text())
        except (OSError, ValueError, *DATABASE_ERRORS) as exc:
            self._say(str(exc))
            return False
        self.erased = True
        self.accept()
        return True


def _offer_recovery(dialog: SignInDialog) -> None:
    reset = ResetAdminAccessDialog(dialog)
    accepted = reset.exec() == QDialog.Accepted
    if accepted and reset.erased:
        # Everything is gone: close this sign-in so the "create the first
        # administrator" screen takes over (see sign_in()).
        dialog.erased = True
        dialog.reject()
    elif accepted and reset.reset_badge:
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
    except DATABASE_ERRORS as exc:
        QMessageBox.warning(parent, tr("recovery.code_title"), str(exc))
        return None
    RecoveryCodeDialog(code, parent).exec()
    return code


def admin_sign_in_dialog(parent=None, cancel_text: str | None = None) -> SignInDialog:
    dialog = SignInDialog(
        tr("admin.auth.sign_in_title"),
        tr("admin.auth.sign_in_sub"),
        lambda badge, pin: account_repository.authenticate(badge, pin, auth.AREA_ADMIN, terminal_name()),
        palette={**CLASSICAL_PALETTE, "on_accent": "#161514"},
        cancel_text=cancel_text or tr("admin.auth.quit"),
        parent=parent,
        extra_action=(tr("signin.forgot"), _offer_recovery),
        server_line=True,
    )

    def server_changed(_url: str) -> None:
        # The new server may have no root yet: close, so sign_in() shows the first-run screen if so.
        dialog.server_changed = True
        dialog.reject()

    dialog.server_line.changed.connect(server_changed)
    return dialog


def sign_in(parent=None, cancel_text: str | None = None) -> Session | None:
    """Run the right dialog and return the Session, or None if cancelled.
    Raises DataAccessError if the database can't be read at all. After a
    "start over" from Forgot your PIN, or after a server address was typed on
    either screen, the loop comes round and shows the screen that fits now."""
    while True:
        if not account_repository.admin_exists():
            dialog = FirstAdminDialog(parent)
        else:
            dialog = admin_sign_in_dialog(parent, cancel_text=cancel_text)
        accepted = dialog.exec() == QDialog.Accepted
        if not accepted and (getattr(dialog, "erased", False) or getattr(dialog, "server_changed", False)):
            continue
        return dialog.session if accepted else None
