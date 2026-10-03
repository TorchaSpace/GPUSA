"""Product CRUD tab: compact data table + add/edit/delete controls.

DataTable is the visual; a row of CompactButtons (Add/Edit/Delete) plus
one reused ProductFormPopup are the controls - see VisualTab's docstring
for why controls live in the tab's own layout rather than floated over
the table.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QWidget

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.data_table import DataTable
from admin_app.gui.components.product_form_popup import ProductFormPopup
from database import product_repository
from database.exceptions import DATABASE_ERRORS, DataAccessError
from shared.gui_kit.visual_tab import VisualTab
from shared.i18n import tr


class ProductManagementTab(VisualTab):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._table = DataTable()
        self.set_visual(self._table)

        self._popup = ProductFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        add_button = CompactButton(tr("admin.add_product"))
        add_button.clicked.connect(self._open_add_popup)
        edit_button = CompactButton(tr("admin.edit_product"))
        edit_button.clicked.connect(self._open_edit_popup)
        delete_button = CompactButton(tr("admin.delete_product"))
        delete_button.clicked.connect(self._delete_selected)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(add_button)
        controls_layout.addWidget(edit_button)
        controls_layout.addWidget(delete_button)
        controls_layout.addStretch()
        self.set_controls(controls)

        self.refresh()

    def refresh(self) -> None:
        self._table.set_products(product_repository.list_all())

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(product=None)

    def _open_edit_popup(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.no_selection_title"), tr("admin.no_selection_body"))
            return
        self._popup.open_or_refresh(product=product)

    def _save_popup(self) -> None:
        product = self._popup.result_product()
        try:
            if self._popup.is_editing():
                product_repository.update(product)
            else:
                product_repository.create(product)
        except (DataAccessError, ValueError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.save_failed_title"), str(exc))
            return
        self.refresh()

    def _delete_selected(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.no_selection_title"), tr("admin.no_selection_body"))
            return

        confirm = QMessageBox.question(
            self,
            tr("admin.confirm_delete_title"),
            tr("admin.confirm_delete_body").format(name=product.name),
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            product_repository.delete(product.barcode)
        except (DataAccessError, ValueError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.save_failed_title"), str(exc))
            return
        self.refresh()
