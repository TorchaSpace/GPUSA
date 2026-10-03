"""Add/edit-dealership popup - mirrors product_form_popup.py's shape
exactly (a RefreshablePopup wrapping a QFormLayout), with `code` playing
the role `barcode` plays there (fixed once the dealership exists, only
editable while adding) and a region QComboBox restricted to
shared.models.DEALERSHIP_REGIONS instead of a free-text field.
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

from database import dealership_repository
from shared.gui_kit.popup_window import RefreshablePopup
from shared.i18n import region_label, tr
from shared.models import DEALERSHIP_REGIONS, Dealership


class DealershipFormPopup(RefreshablePopup):
    """Emits QDialog's own `accepted` signal when Save is pressed AND the
    input passes validation (accept() checks it; a rejected save leaves the
    popup open with what was typed and the reason in the error line) - the
    caller (DealershipsPage) connects to that and reads the result back
    via result_dealership() / is_editing(), same pattern as
    ProductFormPopup. If the page's save then fails, it calls show_error(),
    which brings the popup back with the typed data intact."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_code: str | None = None  # None => Add mode

        self._code_input = QLineEdit()
        self._name_input = QLineEdit()

        self._region_input = QComboBox()
        for region in DEALERSHIP_REGIONS:
            self._region_input.addItem(region_label(region), region)

        self._city_input = QLineEdit()
        self._city_input.setPlaceholderText(tr("admin.dealerships.city_placeholder"))

        self._manager_input = QLineEdit()

        self._active_input = QCheckBox()
        self._active_input.setChecked(True)

        form = QFormLayout()
        form.addRow(tr("admin.form_code"), self._code_input)
        form.addRow(tr("admin.form_name"), self._name_input)
        form.addRow(tr("admin.form_region"), self._region_input)
        form.addRow(tr("admin.form_city"), self._city_input)
        form.addRow(tr("admin.form_manager"), self._manager_input)
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

    def refresh_content(self, dealership: Dealership | None = None) -> None:
        self._editing_code = dealership.code if dealership is not None else None
        self._error.hide()
        self.setWindowTitle(
            tr("admin.edit_dealership") if dealership is not None else tr("admin.add_dealership")
        )

        self._code_input.setText(dealership.code if dealership is not None else "")
        # Code is the natural key - fixed once the dealership exists, so
        # it's only editable while adding, same as ProductFormPopup's
        # barcode field.
        self._code_input.setEnabled(dealership is None)

        self._name_input.setText(dealership.name if dealership is not None else "")
        self._region_input.setCurrentIndex(max(0, self._region_input.findData(dealership.region if dealership is not None else DEALERSHIP_REGIONS[0])))
        self._city_input.setText(dealership.city if dealership is not None else "")
        self._manager_input.setText((dealership.manager_name or "") if dealership is not None else "")
        self._active_input.setChecked(dealership.is_active if dealership is not None else True)

    def is_editing(self) -> bool:
        return self._editing_code is not None

    def problem(self) -> str | None:
        """Why the current input can't be saved (empty / too long code or
        name...), or None. Same rules the repository enforces."""
        try:
            dealership_repository.validate_fields(self.result_dealership())
        except ValueError as exc:
            return str(exc)
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

    def result_dealership(self) -> Dealership:
        """Read the form back out as a Dealership. Caller checks
        is_editing() to decide between dealership_repository.create() and
        .update() - this popup has no database import of its own."""
        return Dealership(
            code=self._code_input.text().strip(),
            name=self._name_input.text().strip(),
            region=self._region_input.currentData() or self._region_input.currentText(),
            city=self._city_input.text().strip(),
            manager_name=self._manager_input.text().strip() or None,
            is_active=self._active_input.isChecked(),
        )
