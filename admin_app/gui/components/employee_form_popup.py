"""Add/edit-employee popup - mirrors dealership_form_popup.py's shape
exactly (a RefreshablePopup wrapping a QFormLayout), with `badge_id`
playing the role `code` plays there (fixed once the employee exists,
only editable while adding), and role/location_type QComboBoxes
restricted to shared.models.EMPLOYEE_ROLES/EMPLOYEE_LOCATION_TYPES.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from database import employee_repository
from shared.gui_kit.popup_window import RefreshablePopup
from shared.i18n import enum_label, tr
from shared.models import EMPLOYEE_LOCATION_TYPES, EMPLOYEE_ROLES, Employee


class EmployeeFormPopup(RefreshablePopup):
    """Emits QDialog's own `accepted` signal when Save is pressed AND the
    input passes validation (accept() checks it; a rejected save leaves the
    popup open with what was typed and the reason in the error line) - the
    caller (WorkforcePage) connects to that and reads the result back via
    result_employee() / is_editing(), same pattern as DealershipFormPopup.
    If the page's save then fails, it calls show_error(), which brings the
    popup back with the typed data intact.

    Location is an editable pick list of the warehouses / dealerships that
    exist (the page hands them over as `locations`); typing a name that
    isn't one is refused."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_badge_id: str | None = None  # None => Add mode
        self._original_place: tuple[str, str] | None = None  # (type, name) of the employee being edited
        self._locations: dict[str, list[str]] | None = None  # None => don't check the name exists

        self._badge_input = QLineEdit()
        self._badge_input.setPlaceholderText("EMP-1040")
        self._name_input = QLineEdit()
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText(tr("admin.workforce.title_placeholder"))

        self._role_input = QComboBox()
        for role in EMPLOYEE_ROLES:
            self._role_input.addItem(enum_label("role", role), role)

        self._location_type_input = QComboBox()
        for kind in EMPLOYEE_LOCATION_TYPES:
            self._location_type_input.addItem(enum_label("location_type", kind), kind)

        self._location_name_input = QComboBox()
        self._location_name_input.setEditable(True)
        self._location_name_input.setInsertPolicy(QComboBox.NoInsert)
        self._location_name_input.lineEdit().setPlaceholderText(tr("admin.workforce.location_hint"))
        self._location_type_input.currentIndexChanged.connect(self._fill_locations)

        self._active_input = QCheckBox()
        self._active_input.setChecked(True)

        form = QFormLayout()
        form.addRow(tr("admin.form_badge"), self._badge_input)
        form.addRow(tr("admin.form_name"), self._name_input)
        form.addRow(tr("admin.form_title"), self._title_input)
        form.addRow(tr("admin.form_role"), self._role_input)
        form.addRow(tr("admin.form_location_type"), self._location_type_input)
        form.addRow(tr("admin.form_location_name"), self._location_name_input)
        form.addRow(tr("admin.form_active"), self._active_input)

        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #b3261e;")
        self._error.hide()
        form.addRow(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def _fill_locations(self, *_args) -> None:
        """Offer the places of the chosen type; keep the typed/current
        name if it belongs to that type's list (or is this employee's
        saved place), else blank it so a Warehouse name can't linger on a
        Dealership employee."""
        kind = self._location_type_input.currentData() or self._location_type_input.currentText()
        names = list((self._locations or {}).get(kind, []))
        keep = self._location_name_input.currentText()
        if self._original_place is not None and self._original_place[0] == kind:
            if self._original_place[1] not in names:
                names.append(self._original_place[1])  # a place saved before this list existed stays selectable
        known = {name.casefold() for name in names}
        self._location_name_input.clear()
        self._location_name_input.addItems(names)
        self._location_name_input.setEditText(keep if keep.casefold() in known else "")

    def refresh_content(self, employee: Employee | None = None, locations: dict[str, list[str]] | None = None) -> None:
        self._editing_badge_id = employee.badge_id if employee is not None else None
        self._original_place = (employee.location_type, employee.location_name) if employee is not None else None
        self._locations = locations
        self._error.hide()
        self.setWindowTitle(
            tr("admin.edit_employee") if employee is not None else tr("admin.add_employee")
        )

        self._badge_input.setText(employee.badge_id if employee is not None else "")
        # Badge id is the natural key - fixed once the employee exists,
        # so it's only editable while adding, same as DealershipFormPopup's
        # code field.
        self._badge_input.setEnabled(employee is None)

        self._name_input.setText(employee.name if employee is not None else "")
        self._title_input.setText((employee.title or "") if employee is not None else "")
        self._role_input.setCurrentIndex(max(0, self._role_input.findData(employee.role if employee is not None else EMPLOYEE_ROLES[0])))
        self._location_type_input.setCurrentIndex(
            max(0, self._location_type_input.findData(
                employee.location_type if employee is not None else EMPLOYEE_LOCATION_TYPES[0]
            ))
        )
        self._location_name_input.setEditText("")
        self._fill_locations()
        self._location_name_input.setEditText(employee.location_name if employee is not None else "")
        self._active_input.setChecked(employee.is_active if employee is not None else True)

    def is_editing(self) -> bool:
        return self._editing_badge_id is not None

    def problem(self) -> str | None:
        """Why the current input can't be saved, or None: empty / too long
        badge, name, location, or a location that isn't an existing place
        of the chosen type (not checked again for an edited employee whose
        place is unchanged). Same rules the repository enforces."""
        employee = self.result_employee()
        try:
            employee_repository.validate_fields(employee)
        except ValueError as exc:
            return str(exc)
        place = (employee.location_type, employee.location_name)
        unchanged = (
            self._original_place is not None
            and place[0] == self._original_place[0]
            and place[1].casefold() == self._original_place[1].casefold()
        )
        if self._locations is not None and not unchanged:
            return employee_repository.location_problem(place[0], place[1], self._locations)
        return None

    def accept(self) -> None:
        problem = self.problem()
        if problem:
            self.show_error(problem)
            return
        self._error.hide()
        super().accept()

    def show_error(self, message: str) -> None:
        """Report a validation/save failure without losing what was typed;
        shows the popup again if it had already closed."""
        self._error.setText(message)
        self._error.show()
        if not self.isVisible():
            self.show()

    def result_employee(self) -> Employee:
        """Read the form back out as an Employee. Caller checks
        is_editing() to decide between employee_repository.create() and
        .update() - this popup has no database import of its own."""
        return Employee(
            badge_id=employee_repository.normalize_badge_id(self._badge_input.text()),
            name=self._name_input.text().strip(),
            title=self._title_input.text().strip() or None,
            role=self._role_input.currentData() or self._role_input.currentText(),
            location_type=self._location_type_input.currentData() or self._location_type_input.currentText(),
            location_name=self._location_name_input.currentText().strip(),
            is_active=self._active_input.isChecked(),
        )
