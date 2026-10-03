"""Themed product table matching the mockup's "Product inventory" table
(Overview and Inventory pages): SKU / Product / Total / Reorder at /
Status, with a colored status dot-equivalent (foreground color on the
Status cell - Qt's model/view table has no per-cell dot glyph without a
custom delegate, so this uses colored text instead, which reads the same
way at a glance).

Deliberately a SEPARATE model/view pair from
admin_app/gui/components/data_table.py's ProductTableModel/DataTable,
which the older Products/Price-Update/Inventory-Health tabs still use -
this one adds the computed Status column and pulls its palette from
admin_app's own CLASSICAL_PALETTE instead of the generic shared theme.
Per-location columns from the mockup (Warehouse A/B/C stock split) are
skipped - see architecture.md and the task list's "visual shell first"
scope note: there's no per-location stock model in the database yet.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from admin_app.theme import CLASSICAL_PALETTE
from shared.models import Product

_COLUMNS = ("SKU", "Product", "Total", "Reorder At", "Status")


def status_for(product: Product) -> tuple[str, str]:
    """Return (label, hex color) - shared by the table and anything else
    (e.g. Inventory's detail panel) that needs the same status logic."""
    p = CLASSICAL_PALETTE
    if product.stock_quantity <= 0:
        return "Out of stock", p["alert_critical"]
    if product.is_below_critical_stock:
        return "Low stock", p["alert_warning"]
    return "In stock", p["alert_success"]


class InventoryTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._products: list[Product] = []

    def set_products(self, products: list[Product]) -> None:
        self.beginResetModel()
        self._products = products
        self.endResetModel()

    def product_at(self, row: int) -> Product:
        return self._products[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._products)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return _COLUMNS[section]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        product = self._products[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == 0:
                return product.barcode
            if column == 1:
                return product.name
            if column == 2:
                return str(product.stock_quantity)
            if column == 3:
                return str(product.critical_stock_level)
            if column == 4:
                return status_for(product)[0]
        elif role == Qt.ForegroundRole and column == 4:
            return QColor(status_for(product)[1])
        elif role == Qt.TextAlignmentRole and column in (2, 3):
            return Qt.AlignRight | Qt.AlignVCenter
        return None


class InventoryTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._table_model = InventoryTableModel(self)
        self.setModel(self._table_model)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)

        p = CLASSICAL_PALETTE
        self.setStyleSheet(
            f"""
            QTableView {{
                background-color: {p['background']};
                alternate-background-color: {p['surface']};
                color: {p['text_primary']};
                gridline-color: {p['border']};
                border: none;
                font-family: {p['font_family_css']};
                font-size: 13px;
                selection-background-color: transparent;
            }}
            QHeaderView::section {{
                background-color: {p['surface']};
                color: {p['text_secondary']};
                border: none;
                border-bottom: 1px solid {p['border']};
                padding: 8px 10px;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            QTableView::item {{
                padding: 4px 10px;
            }}
            QTableView::item:selected {{
                background-color: rgba(225, 173, 102, 30);
                color: {p['text_primary']};
            }}
            """
        )

    def set_products(self, products: list[Product]) -> None:
        self._table_model.set_products(products)

    def selected_product(self) -> Product | None:
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._table_model.product_at(indexes[0].row())
