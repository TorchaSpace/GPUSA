"""Admin > Dealerships > "Reorder levels": the level each product alerts at in ONE shop.

A row per product: what the shop holds now, the product's default level, and the shop's own level
(blank/"Default" = follow the product). Changed rows are saved together; a sale that takes a product to or
under its shop level raises the low-stock alert for the depot's purchasing person.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout

from admin_app.gui.components.compact_button import CompactButton
from database import product_repository, stock_repository
from database.exceptions import DATABASE_ERRORS
from shared import current_session
from shared.formatting import format_int
from shared.i18n import tr
from shared.models import StockLocation

DEFAULT = -1  # spin-box value that means "follow the product"


class ReorderLevelsPopup(QDialog):
    levels_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(620, 540)
        self._location: StockLocation | None = None
        self._rows: list[tuple[str, int | None, QSpinBox]] = []  # barcode, saved override, spin
        self._title = QLabel()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(tr("admin.reorder.filter"))
        self._filter.textChanged.connect(self._apply_filter)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([tr("admin.reorder.col_product"), tr("admin.reorder.col_here"),
                                               tr("admin.reorder.col_default"), tr("admin.reorder.col_level")])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.hide()
        save = CompactButton(tr("admin.reorder.save"), variant="primary")
        save.clicked.connect(self.save)
        close = CompactButton(tr("common.close"))
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(save)
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(QLabel(tr("admin.reorder.hint")))
        layout.addWidget(self._filter)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._message)
        layout.addLayout(buttons)

    def open_for(self, location: StockLocation, name: str) -> None:
        self._location = location
        self.setWindowTitle(tr("admin.reorder.title").format(name=name))
        self._title.setText(f"<b>{tr('admin.reorder.title').format(name=name)}</b>")
        self._filter.clear()
        self._message.hide()
        self.reload()
        self.show()
        self.raise_()

    def reload(self) -> None:
        try:
            here = {p.barcode: p for p in stock_repository.products_at(self._location, include_inactive=False)}
            defaults = {p.barcode: p for p in product_repository.list_active()}
            with_override = self._overrides()
        except DATABASE_ERRORS:
            here, defaults, with_override = {}, {}, {}
        products = sorted(defaults.values(), key=lambda p: p.name.lower())
        self._table.setRowCount(len(products))
        self._rows = []
        for row, product in enumerate(products):
            self._table.setItem(row, 0, QTableWidgetItem(f"{product.barcode} · {product.name}"))
            held = here[product.barcode].stock_quantity if product.barcode in here else 0
            self._table.setItem(row, 1, QTableWidgetItem(format_int(held)))
            self._table.setItem(row, 2, QTableWidgetItem(format_int(product.critical_stock_level)))
            spin = QSpinBox()
            spin.setRange(DEFAULT, 1_000_000)
            spin.setSpecialValueText(tr("admin.reorder.default"))
            override = with_override.get(product.barcode)
            spin.setValue(DEFAULT if override is None else override)
            self._table.setCellWidget(row, 3, spin)
            self._rows.append((product.barcode, override, spin))
        self._apply_filter()

    def _overrides(self) -> dict[str, int]:
        return stock_repository.reorder_overrides_at(self._location)

    def _apply_filter(self) -> None:
        text = self._filter.text().strip().lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            self._table.setRowHidden(row, bool(text) and text not in item.text().lower())

    def changes(self) -> list[tuple[str, int | None]]:
        out = []
        for barcode, saved, spin in self._rows:
            wanted = None if spin.value() == DEFAULT else spin.value()
            if wanted != saved:
                out.append((barcode, wanted))
        return out

    def save(self) -> int:
        changes = self.changes()
        try:
            for barcode, level in changes:
                stock_repository.set_reorder_level(self._location, barcode, level, actor=current_session.actor())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._message.setText(str(exc))
            self._message.show()
            return 0
        self._message.setText(tr("admin.reorder.saved").format(n=len(changes)))
        self._message.show()
        self.reload()
        if changes:
            self.levels_changed.emit()
        return len(changes)
