"""Global price update tab.

DECISION (this stub originally flagged the choice for review before
implementing): a single-product select + price spinbox + Apply button -
not per-cell inline table editing (would need a custom
QStyledItemDelegate for one column, more machinery than this screen's
one job justifies) and not a full add/edit form (that's already
ProductManagementTab's job - this tab is specifically for a fast,
price-only change). This is a one-product-at-a-time first pass; revisit
if a true bulk/percentage-based update ("+5% across all products") is
ever actually requested.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QMessageBox, QWidget

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.data_table import DataTable
from database import product_repository
from database.exceptions import DataAccessError
from shared.gui_kit.visual_tab import VisualTab
from shared.i18n import tr

_MAX_PRICE = 1_000_000


class PriceUpdateTab(VisualTab):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._table = DataTable()
        self.set_visual(self._table)

        self._price_label = QLabel(tr("admin.form_price"))
        self._price_input = QDoubleSpinBox()
        self._price_input.setRange(0, _MAX_PRICE)
        self._price_input.setDecimals(2)

        apply_button = CompactButton(tr("admin.apply_price"))
        apply_button.clicked.connect(self._apply_price)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(self._price_label)
        controls_layout.addWidget(self._price_input)
        controls_layout.addWidget(apply_button)
        controls_layout.addStretch()
        self.set_controls(controls)

        self.refresh()
        self._table.selectionModel().selectionChanged.connect(self._sync_price_input)

    def refresh(self) -> None:
        self._table.set_products(product_repository.list_all())

    def _sync_price_input(self) -> None:
        product = self._table.selected_product()
        if product is not None:
            self._price_input.setValue(product.price)

    def _apply_price(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.no_selection_title"), tr("admin.no_selection_body"))
            return

        product.price = self._price_input.value()
        try:
            product_repository.update(product)
        except DataAccessError as exc:
            QMessageBox.warning(self, tr("admin.save_failed_title"), str(exc))
            return
        self.refresh()
