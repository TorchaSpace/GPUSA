"""Admin > Warehouses > "Distribute stock": split one product's units across
several warehouses and dealerships in a single step ("40 to Istanbul, 25 to
Gebze, 10 to the Harbor dealership").

Pick the product and where the units are now (Unassigned for freshly added
products), type a number against each place, and Distribute moves them all or
none (database.stock_repository.distribute). The table shows what is already at
each place and what is left to place, so the numbers can be checked before
saving. Emits `stock_changed` after a successful write; stays open so the next
product can follow.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from admin_app.gui.components.compact_button import CompactButton
from database import stock_repository
from database.exceptions import DATABASE_ERRORS
from shared import current_session
from shared.formatting import format_int
from shared.i18n import tr
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from shared.warehousing import location_label


class StockDistributePopup(QDialog):
    stock_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("admin.warehouses.dist_title"))
        self.setMinimumSize(560, 520)
        self._places: list[tuple[str, StockLocation]] = []
        self._spins: list[QSpinBox] = []

        self._product_input = QComboBox()
        self._product_input.setEditable(True)
        self._product_input.setInsertPolicy(QComboBox.NoInsert)
        self._from_input = QComboBox()
        self._on_hand = QLabel()
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([tr("admin.warehouses.dist_col_place"),
                                               tr("admin.warehouses.dist_col_now"),
                                               tr("admin.warehouses.dist_col_send")])
        self._table.verticalHeader().hide()
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._left = QLabel()
        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText(tr("admin.warehouses.mv_note_ph"))
        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.hide()

        layout = QVBoxLayout(self)
        hint = QLabel(tr("admin.warehouses.dist_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for label, widget in ((tr("admin.warehouses.mv_product"), self._product_input),
                              (tr("admin.warehouses.mv_from"), self._from_input)):
            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setMinimumWidth(80)
            row.addWidget(caption)
            row.addWidget(widget, stretch=1)
            layout.addLayout(row)
        layout.addWidget(self._on_hand)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._left)
        layout.addWidget(self._note_input)
        layout.addWidget(self._message)
        buttons = QHBoxLayout()
        self._even_button = CompactButton(tr("admin.warehouses.dist_even"))
        self._even_button.clicked.connect(self.split_evenly)
        self._apply_button = CompactButton(tr("admin.warehouses.dist_apply"), variant="primary")
        self._apply_button.clicked.connect(self._save)
        close = CompactButton(tr("common.close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(self._even_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self._apply_button)
        layout.addLayout(buttons)

        self._product_input.currentIndexChanged.connect(self._refresh)
        self._from_input.currentIndexChanged.connect(self._refresh)

    # --- setup -------------------------------------------------------------

    def set_choices(self, products: list[Product], warehouses: list[Warehouse], dealerships: list[Dealership],
                    source: StockLocation | None = None, barcode: str | None = None) -> None:
        self._product_input.blockSignals(True)
        self._product_input.clear()
        for product in products:
            self._product_input.addItem(f"{product.barcode} · {product.name}", product.barcode)
        if barcode:
            index = self._product_input.findData(barcode)
            if index >= 0:
                self._product_input.setCurrentIndex(index)
        self._product_input.blockSignals(False)
        self._places = [(tr("wh.location_site").format(site=w.site_label), w.location) for w in warehouses if w.is_active]
        self._places += [(tr("wh.location_dealer").format(code=d.code, name=d.name), StockLocation.dealership(d.code))
                         for d in dealerships if d.is_active]
        self._places.append((tr("wh.location_unassigned_long"), UNASSIGNED))
        self._from_input.blockSignals(True)
        self._from_input.clear()
        for label, location in self._places:
            self._from_input.addItem(label, location)
        for i in range(self._from_input.count()):
            if self._from_input.itemData(i) == (source or UNASSIGNED):
                self._from_input.setCurrentIndex(i)
        self._from_input.blockSignals(False)
        self._message.hide()
        self._refresh()

    def _barcode(self) -> str | None:
        index = self._product_input.findText(self._product_input.currentText())
        return self._product_input.itemData(index) if index >= 0 else self._product_input.currentData()

    def _source(self) -> StockLocation | None:
        return self._from_input.currentData()

    def _available(self) -> int:
        barcode, source = self._barcode(), self._source()
        if barcode is None or source is None:
            return 0
        try:
            return stock_repository.quantity_at(source, barcode)
        except DATABASE_ERRORS:
            return 0

    def _refresh(self) -> None:
        """Rebuild the rows (every place except the source) with what is there now."""
        barcode, source = self._barcode(), self._source()
        available = self._available()
        self._on_hand.setText(tr("admin.warehouses.mv_on_hand").format(n=format_int(available)))
        rows = [(label, loc) for label, loc in self._places if loc != source and not loc.is_unassigned]
        self._table.setRowCount(len(rows))
        self._spins = []
        for row, (label, location) in enumerate(rows):
            try:
                now = stock_repository.quantity_at(location, barcode) if barcode else 0
            except DATABASE_ERRORS:
                now = 0
            self._table.setItem(row, 0, QTableWidgetItem(label))
            self._table.setItem(row, 1, QTableWidgetItem(format_int(now)))
            spin = QSpinBox()
            spin.setRange(0, 10_000_000)
            spin.valueChanged.connect(self._update_left)
            self._table.setCellWidget(row, 2, spin)
            self._spins.append(spin)
        self._rows = rows
        self._update_left()

    def _update_left(self) -> None:
        left = self._available() - sum(s.value() for s in self._spins)
        if left >= 0:
            self._left.setText(tr("admin.warehouses.dist_left").format(n=format_int(left)))
        else:
            source = self._from_input.currentText()
            self._left.setText(tr("admin.warehouses.dist_over").format(n=format_int(-left), source=source))
        self._apply_button.setEnabled(left >= 0 and any(s.value() for s in self._spins))

    def split_evenly(self) -> None:
        """Share what the source holds equally between the warehouses (dealerships stay at 0)."""
        targets = [i for i, (_label, loc) in enumerate(self._rows) if loc.kind == "warehouse"]
        for spin in self._spins:
            spin.setValue(0)
        if not targets:
            return
        share, extra = divmod(self._available(), len(targets))
        for n, index in enumerate(targets):
            self._spins[index].setValue(share + (1 if n < extra else 0))

    def allocations(self) -> list[tuple[StockLocation, int]]:
        return [(loc, spin.value()) for (_label, loc), spin in zip(self._rows, self._spins) if spin.value()]

    # --- save --------------------------------------------------------------

    def _show(self, text: str) -> None:
        self._message.setText(text)
        self._message.show()

    def _save(self) -> None:
        barcode, source = self._barcode(), self._source()
        if barcode is None or source is None:
            self._show(tr("admin.warehouses.mv_pick"))
            return
        try:
            placed = stock_repository.distribute(source, barcode, self.allocations(), self._note_input.text(),
                                                 actor=current_session.actor())
        except (ValueError, *DATABASE_ERRORS) as exc:  # not enough stock, over capacity, inactive warehouse
            self._show(str(exc))
            return
        self._show(tr("admin.warehouses.dist_done").format(qty=format_int(placed), barcode=barcode,
                                                           source=location_label(source)))
        self._note_input.clear()
        self._refresh()
        self.stock_changed.emit()
