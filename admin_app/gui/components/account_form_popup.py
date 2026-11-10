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
    """Root adds an IAM user: the account type (Admin, depot manager or cashier), the badge ID and the PIN.
    The badge box suggests the people who have no account yet (added at the depot / till Staff page). A badge
    nobody has yet is a new person: two more rows appear in place - the name and, for a depot manager or a
    cashier, the warehouse or dealership they work at - and the person is added together with the account, so
    a fresh installation needs nothing but root to get its first Admin, depot manager and cashier.

    `places` is employee_repository.location_choices(); `taken` the badges that already have an account
    (typed again they are not "new" - the save then says the account exists)."""

    def __init__(self, employees: list[tuple[str, str]], parent=None, places: dict[str, list[str]] | None = None,
                 taken: list[str] | None = None):
        super().__init__(tr("admin.settings.add_title"), parent)
        self._places = places or {}
        self._known = {auth.normalize_badge_id(badge) for badge, _name in employees}
        self._known |= {auth.normalize_badge_id(badge) for badge in taken or []}
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
        self.new_note = QLabel(tr("admin.settings.new_person"))
        self.new_note.setWordWrap(True)
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.place = QComboBox()
        self.form.addRow(tr("admin.settings.role"), self.role)
        self.form.addRow(tr("admin.settings.badge"), self.badge)
        self.form.addRow(self.new_note)
        self.form.addRow(tr("admin.settings.new_name"), self.name)
        self.form.addRow(tr("admin.settings.new_place"), self.place)
        self.badge.textChanged.connect(self._sync_new_person)
        self.role.currentIndexChanged.connect(self._sync_new_person)
        self._sync_new_person()
        self.form.addRow(tr("admin.settings.pin"), self.pin)
        self.form.addRow(tr("admin.settings.pin_again"), self.pin_again)
        self.form.addRow(hint)
        self._finish()

    def is_new_person(self) -> bool:
        badge = auth.normalize_badge_id(self.badge.text())
        return bool(badge) and badge not in self._known

    def _place_kind(self) -> str | None:
        """Which kind of place the chosen account type belongs to (None: an Admin, at head office)."""
        return {"depot_manager": "Warehouse", "cashier": "Dealership"}.get(self.role.currentData())

    def _sync_new_person(self) -> None:
        new, kind = self.is_new_person(), self._place_kind()
        self.new_note.setVisible(new)
        self.form.setRowVisible(self.name, new)
        self.form.setRowVisible(self.place, new and kind is not None)
        wanted = self._places.get(kind, []) if kind else []
        if [self.place.itemText(i) for i in range(self.place.count())] != wanted:
            self.place.clear()
            self.place.addItems(wanted)
        self.adjustSize()

    def _try_accept(self) -> None:
        if not self.badge.text().strip():
            self._fail(tr("err.badge_required"))
            return
        if self.is_new_person():
            if not self.name.text().strip():
                self._fail(tr("admin.settings.new_name_required"))
                return
            kind = self._place_kind()
            if kind is not None and not self.place.currentText():
                self._fail(tr("admin.settings.new_place_required_depot" if kind == "Warehouse"
                              else "admin.settings.new_place_required_shop"))
                return
        if self._check_new_pin(self.role.currentData()):
            self.accept()

    def values(self) -> tuple[str, str, str]:
        return auth.normalize_badge_id(self.badge.text()), self.role.currentData(), self.pin.text()

    def new_person(self) -> tuple[str, str] | None:
        """(name, place) when the badge is a new person's, else None."""
        if not self.is_new_person():
            return None
        return " ".join(self.name.text().split()), self.place.currentText() if self._place_kind() else ""


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
