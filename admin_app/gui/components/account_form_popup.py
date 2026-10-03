"""Settings' two account popups:
- AddAccountPopup: pick an employee (from Workforce, without an account
  yet), a role, and their first PIN (typed twice).
- PinPopup: set a new PIN (an administrator resetting someone's, or you
  changing your own - then the current PIN is asked too).
Both validate with shared.auth.pin_problem() before closing and leave the
database write to SettingsPage.
"""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit

from shared import auth


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
            self._fail("The two PINs don't match.")
            return False
        problem = auth.pin_problem(self.pin.text(), role)
        if problem:
            self._fail(problem)
            return False
        return True


class AddAccountPopup(_Base):
    def __init__(self, employees: list[tuple[str, str]], parent=None):
        super().__init__("Add sign-in account", parent)
        self.employee = QComboBox()
        for badge, name in employees:
            self.employee.addItem(f"{name} · {badge}", badge)
        self.role = QComboBox()
        for role in auth.ROLES:
            self.role.addItem(auth.role_label(role), role)
        self.role.setCurrentIndex(auth.ROLES.index("cashier"))
        self.pin = _pin_field()
        self.pin_again = _pin_field()
        hint = QLabel(f"PIN: digits only, {auth.MIN_PIN_LENGTH}+ ({auth.MIN_ADMIN_PIN_LENGTH}+ for an administrator).")
        hint.setWordWrap(True)
        self.form.addRow("Employee", self.employee)
        self.form.addRow("Role", self.role)
        self.form.addRow("PIN", self.pin)
        self.form.addRow("PIN again", self.pin_again)
        self.form.addRow(hint)
        self._finish()

    def _try_accept(self) -> None:
        if self.employee.currentData() is None:
            self._fail("Everyone active in Workforce already has an account - add the employee there first.")
            return
        if self._check_new_pin(self.role.currentData()):
            self.accept()

    def values(self) -> tuple[str, str, str]:
        return self.employee.currentData(), self.role.currentData(), self.pin.text()


class PinPopup(_Base):
    def __init__(self, who: str, role: str, ask_current: bool, parent=None):
        super().__init__(f"New PIN · {who}", parent)
        self._role = role
        self.current = _pin_field() if ask_current else None
        if self.current is not None:
            self.form.addRow("Current PIN", self.current)
        self.pin = _pin_field()
        self.pin_again = _pin_field()
        self.form.addRow("New PIN", self.pin)
        self.form.addRow("New PIN again", self.pin_again)
        self._finish()

    def _try_accept(self) -> None:
        if self.current is not None and not self.current.text():
            self._fail("Enter your current PIN.")
            return
        if self._check_new_pin(self._role):
            self.accept()
