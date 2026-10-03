"""Dense QTableView-based data grid used for product lists and report rows.

A thin QTableView wrapper around ProductTableModel, a QAbstractTableModel
over a list of shared.models.Product - the model/view table widget
PySide6 was chosen for (see architecture.md's "Why PySide6"), so
sorting/selection come for free instead of hand-rolled. Both
ProductManagementTab and InventoryHealthTab reuse this exact class - they
show the identical column set, just with different row sources (all
products vs. only critical-stock ones) and, for ProductManagementTab, a
selection the Add/Edit/Delete buttons act on.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import QAbstractItemView, QTableView

from shared.i18n import tr
from shared.models import Product

_COLUMN_KEYS = ("barcode", "name", "price", "stock", "critical_level")


def _columns() -> tuple[str, ...]:
    return tuple(tr(f"admin.form_{key}") for key in _COLUMN_KEYS)


class ProductTableModel(QAbstractTableModel):
    """Read-mostly table model over a list of Product.

    set_products() replaces the whole row set (a fresh repository query
    result) and refreshes the view - there is no in-place cell editing
    here. Edits go through ProductManagementTab's add/edit popup (or
    PriceUpdateTab's price field), each followed by a fresh
    set_products() call, so what's on screen is always exactly what the
    last repository query returned - never a stale, hand-patched row.
    """

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
        return 0 if parent.isValid() else len(_COLUMN_KEYS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return _columns()[section]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        product = self._products[index.row()]
        column = index.column()
        if column == 0:
            return product.barcode
        if column == 1:
            return product.name
        if column == 2:
            return f"{product.price:.2f}"
        if column == 3:
            return str(product.stock_quantity)
        if column == 4:
            return str(product.critical_stock_level)
        return None


class DataTable(QTableView):
    """Single-selection product grid. No direct in-cell editing - see
    ProductTableModel's docstring for why edits always go through a
    popup/field followed by a full reload instead."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._table_model = ProductTableModel(self)
        self.setModel(self._table_model)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)

    def set_products(self, products: list[Product]) -> None:
        self._table_model.set_products(products)

    def selected_product(self) -> Product | None:
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._table_model.product_at(indexes[0].row())
