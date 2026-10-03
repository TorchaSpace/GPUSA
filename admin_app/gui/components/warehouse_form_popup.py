"""Add/edit-warehouse popup - same shape as dealership_form_popup.py: a
RefreshablePopup around a QFormLayout, `code` fixed once the warehouse
exists. Capacity is optional (empty = not set); the popup has no
database import of its own - WarehousesPage saves the result."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox

from shared.i18n import tr
from shared.gui_kit.popup_window import RefreshablePopup
from shared.models import Warehouse


class WarehouseFormPopup(RefreshablePopup):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_code: str | None = None

        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("WH-01")
        self._name_input = QLineEdit()
        self._city_input = QLineEdit()
        self._capacity_input = QLineEdit()
        self._capacity_input.setPlaceholderText(tr("admin.warehouses.form_capacity_ph"))
        self._docks_input = QSpinBox()
        self._docks_input.setRange(0, 999)
        self._active_input = QCheckBox()
        self._active_input.setChecked(True)
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.hide()

        form = QFormLayout()
        form.addRow(tr("admin.warehouses.form_code"), self._code_input)
        form.addRow(tr("admin.warehouses.form_name"), self._name_input)
        form.addRow(tr("admin.warehouses.form_city"), self._city_input)
        form.addRow(tr("admin.warehouses.form_capacity"), self._capacity_input)
        form.addRow(tr("admin.warehouses.form_docks"), self._docks_input)
        form.addRow(tr("admin.warehouses.form_active"), self._active_input)
        form.addRow(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def refresh_content(self, warehouse: Warehouse | None = None) -> None:
        self._editing_code = warehouse.code if warehouse is not None else None
        self.setWindowTitle(
            tr("admin.warehouses.form_edit") if warehouse is not None else tr("admin.warehouses.form_add")
        )
        self._code_input.setText(warehouse.code if warehouse else "")
        self._code_input.setEnabled(warehouse is None)
        self._name_input.setText(warehouse.name if warehouse else "")
        self._city_input.setText(warehouse.city if warehouse else "")
        self._capacity_input.setText(str(warehouse.capacity_units) if warehouse and warehouse.capacity_units else "")
        self._docks_input.setValue(warehouse.docks if warehouse else 0)
        self._active_input.setChecked(warehouse.is_active if warehouse else True)
        self._error.hide()

    def _capacity(self) -> int | None:
        text = self._capacity_input.text().strip().replace(",", "").replace(".", "")
        if not text:
            return None
        value = int(text)  # ValueError for non-numbers
        if value <= 0:
            raise ValueError
        return value

    def _try_accept(self) -> None:
        try:
            self._capacity()
        except ValueError:
            self._error.setText(tr("admin.warehouses.form_err_capacity"))
            self._error.show()
            return
        if not self._code_input.text().strip() or not self._name_input.text().strip():
            self._error.setText(tr("admin.warehouses.form_err_required"))
            self._error.show()
            return
        self.accept()

    def is_editing(self) -> bool:
        return self._editing_code is not None

    def result_warehouse(self) -> Warehouse:
        return Warehouse(
            code=self._code_input.text().strip(),
            name=self._name_input.text().strip(),
            city=self._city_input.text().strip(),
            capacity_units=self._capacity(),
            docks=self._docks_input.value(),
            is_active=self._active_input.isChecked(),
        )
