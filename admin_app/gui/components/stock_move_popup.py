"""Admin > Warehouses > "Move / count stock": the two stock corrections
an administrator makes by hand.

- Move: take N units of a product from one place (a warehouse, a
  dealership, or Unassigned) and put them at another in one step -
  placing stock from before per-location tracking, or rebalancing two
  warehouses. The company total doesn't change. (Goods that travel by
  truck go through a depot's Shipments page instead.)
- Count: someone counted the shelf - set the level to what's really
  there; the difference is logged as a stock-count movement.

Writes go through database.stock_repository and stay open on an error
(e.g. not enough at the source), so nothing typed is lost. Emits
`stock_changed` after each successful write.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QWidget,
)

from database import stock_repository
from database.exceptions import DataAccessError
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from shared import current_session


class StockMovePopup(QDialog):
    stock_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move / count stock")
        self.setMinimumWidth(460)

        self._move_radio = QRadioButton("Move between locations")
        self._count_radio = QRadioButton("Record a stock count")
        self._move_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self._move_radio)
        group.addButton(self._count_radio)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._move_radio)
        mode_row.addWidget(self._count_radio)
        mode_row.addStretch(1)
        mode = QWidget()
        mode.setLayout(mode_row)

        self._product_input = QComboBox()
        self._product_input.setEditable(True)
        self._product_input.setInsertPolicy(QComboBox.NoInsert)
        self._from_input = QComboBox()
        self._to_input = QComboBox()
        self._on_hand = QLabel()
        self._qty_input = QSpinBox()
        self._qty_input.setRange(0, 10_000_000)
        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText("optional - why")
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.hide()

        form = QFormLayout()
        form.addRow(mode)
        form.addRow("Product", self._product_input)
        self._from_label = QLabel("From")
        form.addRow(self._from_label, self._from_input)
        form.addRow("", self._on_hand)
        self._to_label = QLabel("To")
        form.addRow(self._to_label, self._to_input)
        self._qty_label = QLabel("Quantity")
        form.addRow(self._qty_label, self._qty_input)
        form.addRow("Note", self._note_input)
        form.addRow(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

        for signal in (self._product_input.currentIndexChanged, self._from_input.currentIndexChanged):
            signal.connect(self._update_on_hand)
        self._move_radio.toggled.connect(self._apply_mode)
        self._apply_mode()

    # --- setup -------------------------------------------------------------

    def set_choices(self, products: list[Product], warehouses: list[Warehouse], dealerships: list[Dealership],
                    source: StockLocation | None = None, destination: StockLocation | None = None) -> None:
        """Fill the pickers. `source`/`destination` preselect locations."""
        self._product_input.blockSignals(True)
        self._product_input.clear()
        for product in products:
            self._product_input.addItem(f"{product.barcode} · {product.name}", product.barcode)
        self._product_input.blockSignals(False)
        locations = [(f"Warehouse · {w.site_label}", w.location) for w in warehouses]
        locations += [(f"Dealership · {d.code} · {d.name}", StockLocation.dealership(d.code)) for d in dealerships]
        locations.append(("Unassigned (not placed yet)", UNASSIGNED))
        for combo in (self._from_input, self._to_input):
            combo.blockSignals(True)
            combo.clear()
            for label, location in locations:
                combo.addItem(label, location)
            combo.blockSignals(False)
        self._select(self._from_input, source or UNASSIGNED)
        self._select(self._to_input, destination or (warehouses[0].location if warehouses else UNASSIGNED))
        self._error.hide()
        self._update_on_hand()

    @staticmethod
    def _select(combo: QComboBox, location: StockLocation) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == location:
                combo.setCurrentIndex(i)
                return

    def _apply_mode(self) -> None:
        moving = self._move_radio.isChecked()
        self._from_label.setText("From" if moving else "Location")
        self._to_label.setVisible(moving)
        self._to_input.setVisible(moving)
        self._qty_label.setText("Quantity" if moving else "Counted")
        self._qty_input.setMinimum(1 if moving else 0)
        self._update_on_hand()

    def _barcode(self) -> str | None:
        index = self._product_input.findText(self._product_input.currentText())
        return self._product_input.itemData(index) if index >= 0 else self._product_input.currentData()

    def _update_on_hand(self) -> None:
        barcode, location = self._barcode(), self._from_input.currentData()
        if barcode is None or location is None:
            self._on_hand.setText("")
            return
        try:
            here = stock_repository.quantity_at(location, barcode)
        except DataAccessError:
            self._on_hand.setText("")
            return
        self._on_hand.setText(f"{here:,} on hand there")
        if self._count_radio.isChecked():
            self._qty_input.setValue(here)

    # --- save --------------------------------------------------------------

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def _save(self) -> None:
        barcode = self._barcode()
        source = self._from_input.currentData()
        if barcode is None or source is None:
            self._fail("Pick a product and a location.")
            return
        quantity, note = self._qty_input.value(), self._note_input.text()
        try:
            if self._move_radio.isChecked():
                destination = self._to_input.currentData()
                stock_repository.transfer(source, destination, barcode, quantity, note, actor=current_session.actor())
                done = f"Moved {quantity:,} × {barcode} from {source.label} to {destination.label}."
            else:
                diff = stock_repository.set_count(source, barcode, quantity, note, actor=current_session.actor())
                done = (f"{source.label}: count matches ({quantity:,})." if diff == 0
                        else f"{source.label}: {barcode} set to {quantity:,} ({diff:+,}).")
        except (DataAccessError, ValueError) as exc:
            self._fail(str(exc))
            return
        self._error.setText(done)
        self._error.show()
        self._note_input.clear()
        self._update_on_hand()
        self.stock_changed.emit()
