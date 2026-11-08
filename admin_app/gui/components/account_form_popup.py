"""Settings' two account popups:
- AddAccountPopup: the account type (depot manager / cashier), the badge ID
  and the first PIN (typed twice).
- PinPopup: set a new PIN (an administrator resetting someone's, or you
  changing your own - then the current PIN is asked too).
Both validate with shared.auth.pin_problem() before closing and leave the
database write to SettingsPage.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit

from shared import auth
from shared.i18n import tr


def _pin_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setMaxLength(auth.MAX_PIN_LENGTH)
    return field


class _Base(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.form = QFormLayout(self)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()

    def _finish(self) -> None:
        self.form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _check_new_pin(self, role: str) -> bool:
        if self.pin.text() != self.pin_again.text():
            self._fail(tr("admin.settings.pin_mismatch"))
            return False
        problem = auth.pin_problem(self.pin.text(), role)
        if problem:
            self._fail(problem)
            return False
        return True


class AddAccountPopup(_Base):
    """Root adds an IAM user in three steps: the account type (depot manager or cashier), the badge ID and the
    PIN. The badge must belong to someone already added at the depot / till Staff page (they carry the place and
    the national ID); the badge box suggests the people who have no account yet."""

    def __init__(self, employees: list[tuple[str, str]], parent=None):
        super().__init__(tr("admin.settings.add_title"), parent)
        self.role = QComboBox()
        for role in auth.IAM_ROLES:  # IAM users only - there is a single root account
            self.role.addItem(auth.role_label(role), role)
        self.role.setCurrentIndex(auth.IAM_ROLES.index("cashier"))
        self.badge = QLineEdit()
        self.badge.setMaxLength(32)
        suggestions = [badge for badge, _name in employees]
        completer = QCompleter(suggestions, self.badge)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.badge.setCompleter(completer)
        self.pin = _pin_field()
        self.pin_again = _pin_field()
        hint = QLabel(tr("admin.settings.add_hint").format(min=auth.MIN_PIN_LENGTH))
        hint.setWordWrap(True)
        self.form.addRow(tr("admin.settings.role"), self.role)
        self.form.addRow(tr("admin.settings.badge"), self.badge)
        self.form.addRow(tr("admin.settings.pin"), self.pin)
        self.form.addRow(tr("admin.settings.pin_again"), self.pin_again)
        self.form.addRow(hint)
        self._finish()

    def _try_accept(self) -> None:
        if not self.badge.text().strip():
            self._fail(tr("err.badge_required"))
            return
        if self._check_new_pin(self.role.currentData()):
            self.accept()

    def values(self) -> tuple[str, str, str]:
        return auth.normalize_badge_id(self.badge.text()), self.role.currentData(), self.pin.text()


class PinPopup(_Base):
    def __init__(self, who: str, role: str, ask_current: bool, parent=None):
        super().__init__(tr("admin.settings.new_pin_title").format(who=who), parent)
        self._role = role
        self.current = _pin_field() if ask_current else None
        if self.current is not None:
            self.form.addRow(tr("admin.settings.current_pin"), self.current)
        self.pin = _pin_field()
        self.pin_again = _pin_field()
        self.form.addRow(tr("admin.settings.new_pin"), self.pin)
        self.form.addRow(tr("admin.settings.new_pin_again"), self.pin_again)
        self._finish()

    def _try_accept(self) -> None:
        if self.current is not None and not self.current.text():
            self._fail(tr("admin.settings.enter_current"))
            return
        if self._check_new_pin(self._role):
            self.accept()
