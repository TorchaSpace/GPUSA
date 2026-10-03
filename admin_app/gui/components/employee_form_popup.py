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
    QLineEdit,
)

from shared.gui_kit.popup_window import RefreshablePopup
from shared.i18n import tr
from shared.models import EMPLOYEE_LOCATION_TYPES, EMPLOYEE_ROLES, Employee


class EmployeeFormPopup(RefreshablePopup):
    """Emits QDialog's own `accepted` signal when Save is pressed - the
    caller (WorkforcePage) connects to that and reads the result back via
    result_employee() / is_editing(), same pattern as DealershipFormPopup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_badge_id: str | None = None  # None => Add mode

        self._badge_input = QLineEdit()
        self._badge_input.setPlaceholderText("EMP-1040")
        self._name_input = QLineEdit()
        self._title_input = QLineEdit()
        self._title_input.setPlaceholderText("Forklift operator (optional)")

        self._role_input = QComboBox()
        self._role_input.addItems(EMPLOYEE_ROLES)

        self._location_type_input = QComboBox()
        self._location_type_input.addItems(EMPLOYEE_LOCATION_TYPES)

        self._location_name_input = QLineEdit()
        self._location_name_input.setPlaceholderText("İstanbul Merkez")

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

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def refresh_content(self, employee: Employee | None = None) -> None:
        self._editing_badge_id = employee.badge_id if employee is not None else None
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
        self._role_input.setCurrentText(employee.role if employee is not None else EMPLOYEE_ROLES[0])
        self._location_type_input.setCurrentText(
            employee.location_type if employee is not None else EMPLOYEE_LOCATION_TYPES[0]
        )
        self._location_name_input.setText(employee.location_name if employee is not None else "")
        self._active_input.setChecked(employee.is_active if employee is not None else True)

    def is_editing(self) -> bool:
        return self._editing_badge_id is not None

    def result_employee(self) -> Employee:
        """Read the form back out as an Employee. Caller checks
        is_editing() to decide between employee_repository.create() and
        .update() - this popup has no database import of its own."""
        return Employee(
            badge_id=self._badge_input.text().strip(),
            name=self._name_input.text().strip(),
            title=self._title_input.text().strip() or None,
            role=self._role_input.currentText(),
            location_type=self._location_type_input.currentText(),
            location_name=self._location_name_input.text().strip(),
            is_active=self._active_input.isChecked(),
        )
