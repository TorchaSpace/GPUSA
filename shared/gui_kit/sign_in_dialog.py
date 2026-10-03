"""The badge + PIN sign-in dialog all three apps use, styled with the
calling app's own palette (Classical / Organic / Industry) so it looks
native in each.

It knows nothing about the database: the caller passes `authenticate`, a
callable (badge, pin) -> Session that raises an exception whose text is
safe to show (database.exceptions.AuthError and friends). That keeps
shared/ free of a database import and makes the dialog trivial to test.

Two modes:
- sign in: badge + PIN, both typed;
- confirm: `fixed_badge` given - the badge is shown, not editable, only
  the PIN is asked (the depot Manager Portal re-checking the signed-in
  manager).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from shared.i18n import tr
from shared.auth import MAX_PIN_LENGTH, Session, normalize_badge_id


class SignInDialog(QDialog):
    def __init__(
        self,
        title: str,
        subtitle: str,
        authenticate: Callable[[str, str], Session],
        palette: dict,
        fixed_badge: str | None = None,
        cancel_text: str | None = None,
        parent=None,
        extra_action: tuple[str, Callable[["SignInDialog"], None]] | None = None,
    ):
        super().__init__(parent)
        p = palette
        self._authenticate = authenticate
        self._fixed_badge = fixed_badge
        self.session: Session | None = None
        self.setWindowTitle(title)
        self.setModal(True)
        # Fixed width, so wrapped subtitle/error text gets the height it needs.
        self.setFixedWidth(420)
        self.setObjectName("signInDialog")
        radius = p.get("radius_md", "4px")
        self.setStyleSheet(
            f"""
            #signInDialog {{ background-color: {p['background']}; }}
            QLabel {{ background: transparent; border: none; }}
            QLineEdit {{ background-color: {p['surface']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; border-radius: {radius}; padding: 8px 10px; font-size: 15px; }}
            QLineEdit:focus {{ border: 1px solid {p['accent']}; }}
            QPushButton {{ border-radius: {radius}; padding: 8px 16px; font-size: 14px; }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet(f"font-family: '{p.get('font_heading', '')}'; font-size: 24px; color: {p['text_primary']};")
        layout.addWidget(heading)
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(sub)
        layout.addSpacing(6)

        self.badge_input = QLineEdit()
        self.badge_input.setPlaceholderText(tr("signin.badge_placeholder"))
        if fixed_badge:
            self.badge_input.setText(fixed_badge)
            self.badge_input.setReadOnly(True)
        layout.addWidget(self._caption(tr("signin.badge"), p))
        layout.addWidget(self.badge_input)

        self.pin_input = QLineEdit()
        self.pin_input.setPlaceholderText(tr("signin.pin"))
        self.pin_input.setEchoMode(QLineEdit.Password)
        self.pin_input.setMaxLength(MAX_PIN_LENGTH)
        self.pin_input.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d*"), self.pin_input))
        layout.addWidget(self._caption(tr("signin.pin"), p))
        layout.addWidget(self.pin_input)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"font-size: 13px; color: {p['alert_critical']};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        if extra_action is not None:
            # A quiet link under the fields, e.g. "Forgot your PIN?".
            label, callback = extra_action
            self.extra_button = QPushButton(label)
            self.extra_button.setFlat(True)
            self.extra_button.setCursor(Qt.PointingHandCursor)
            self.extra_button.setStyleSheet(
                f"QPushButton {{ background: transparent; border: none; color: {p['accent']}; "
                f"text-align: left; padding: 2px 0; font-size: 13px; }}"
                f"QPushButton:hover {{ text-decoration: underline; }}"
            )
            self.extra_button.clicked.connect(lambda: callback(self))
            layout.addWidget(self.extra_button)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton(cancel_text or tr("common.cancel"))
        self.cancel_button.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {p['text_secondary']}; border: 1px solid {p['border']}; }}"
        )
        self.cancel_button.clicked.connect(self.reject)
        self.sign_in_button = QPushButton(tr("signin.unlock") if fixed_badge else tr("signin.sign_in"))
        self.sign_in_button.setDefault(True)
        self.sign_in_button.setStyleSheet(
            f"QPushButton {{ background-color: {p['accent']}; color: {p.get('on_accent', '#ffffff')}; border: none; }}"
        )
        self.sign_in_button.clicked.connect(self.try_sign_in)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.sign_in_button)
        layout.addLayout(buttons)

        self.badge_input.returnPressed.connect(self.pin_input.setFocus)
        self.pin_input.returnPressed.connect(self.try_sign_in)
        (self.pin_input if fixed_badge else self.badge_input).setFocus()

    @staticmethod
    def _caption(text: str, p: dict) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        return label

    def try_sign_in(self) -> None:
        badge, pin = normalize_badge_id(self.badge_input.text()), self.pin_input.text()
        if not badge or not pin:
            self._fail(tr("signin.enter_both"))
            return
        if not self.sign_in_button.isEnabled():  # already checking: a double click must not count two wrong PINs
            return
        self.sign_in_button.setEnabled(False)
        try:
            self.session = self._authenticate(badge, pin)
        except Exception as exc:  # AuthError & co: the message is written to be shown
            self._fail(str(exc) or tr("signin.failed"))
            return
        finally:
            self.sign_in_button.setEnabled(True)
        self.accept()

    def _fail(self, message: str) -> None:
        self.pin_input.clear()
        self.pin_input.setFocus()
        self.error_label.setText(message)
        self.error_label.show()
        self.adjustSize()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.adjustSize()
