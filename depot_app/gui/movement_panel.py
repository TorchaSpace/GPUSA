"""One Inbound or Outbound stock-movement panel: a log form + a live
activity table. Recreates the Floor App mockup's side-by-side Inbound/
Outbound panels (two instances of this same widget, not a single
direction-toggle form) - see depot_app/gui/main_window.py.

Real, not a placeholder: wired through depot_app/services/receiving_service.py
/dispatch_service.py, which call database.inventory_repository.receive_stock()
/dispatch_stock() - this is depot_app's own equivalent of pos_app's
checkout flow.

KNOWN SIMPLIFICATION: the mockup's form has a separate "PO/ASN ref" (or
"Order #") field AND a separate "Put-away bin"/"Dock door" field.
stock_movements has a single free-text `note` column (see
database/schema.sql) - there is no bin/dock/location concept in the
schema. Both mockup fields are kept as separate visual inputs (for
fidelity) but combined into one note string on save
(f"{ref} · {bin_or_dock}"), and shown back as a single "Ref / bin"
column in the activity table. A real bin/location column is future
scope, not invented here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.exceptions import DataAccessError, InsufficientStockError, ProductNotFoundError
from database.inventory_repository import list_recent_movements
from shared.models import UNASSIGNED, StockLocation
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.services import dispatch_service, receiving_service
from depot_app.theme import FONT_HEADING, INDUSTRY_PALETTE

_DIRECTIONS = {
    "receive": {
        "glyph": "↓",
        "title": "Inbound",
        "unit_noun": "receipts",
        "ref_label": "PO / ASN ref",
        "ref_placeholder": "PO-20931",
        "loc_label": "Put-away bin",
        "loc_placeholder": "A-03",
        "submit_label": "Log receipt",
        "unknown_sku_error": "Unknown SKU. Add the product in Admin first.",
    },
    "dispatch": {
        "glyph": "↑",
        "title": "Outbound",
        "unit_noun": "picks",
        "ref_label": "Order #",
        "ref_placeholder": "SO-58812",
        "loc_label": "Dock door",
        "loc_placeholder": "D-07",
        "submit_label": "Log shipment",
        "unknown_sku_error": "Unknown SKU. Add the product in Admin first.",
    },
}


class MovementPanel(QWidget):
    """`direction` is "receive" or "dispatch". Emits `movement_logged`
    after a successful write, so main_window.py can refresh the low
    stock banner (and the sibling panel, if the same SKU affects both)."""

    movement_logged = Signal()

    def __init__(self, direction: str, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        if direction not in _DIRECTIONS:
            raise ValueError(f"Unknown direction {direction!r}")
        self._direction = direction
        self._location = location  # this depot's warehouse - stock moves in/out of it only
        self._spec = _DIRECTIONS[direction]
        p = INDUSTRY_PALETTE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        card = BlueprintFrame(tick_color=p["text_primary"])
        card.setStyleSheet(f"background-color: {p['surface']}; border: 1px solid {p['border']};")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        glyph = QLabel(self._spec["glyph"])
        glyph.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 20px; font-weight: 600; color: {p['accent']};")
        header.addWidget(glyph)
        title = QLabel(self._spec["title"].upper())
        title.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 1px; "
            f"font-size: 16px; color: {p['text_primary']};"
        )
        header.addWidget(title)
        header.addStretch(1)
        self._summary_label = QLabel()
        self._summary_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        header.addWidget(self._summary_label)
        card_layout.addLayout(header)

        card_layout.addWidget(self._build_form())

        self._error_label = QLabel()
        self._error_label.setStyleSheet(f"color: {p['text_primary']}; background-color: #fff6d6; "
                                         f"border: 1px solid #f4b400; padding: 6px 10px; font-size: 12px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        card_layout.addWidget(self._error_label)

        self._table = self._build_table()
        card_layout.addWidget(self._table, stretch=1)

        outer.addWidget(card)
        self.reload()

    def _build_form(self) -> QWidget:
        p = INDUSTRY_PALETTE
        form = QHBoxLayout()
        form.setSpacing(8)

        input_style = (
            f"QLineEdit, QSpinBox {{ background-color: {p['background']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; border-radius: 0; padding: 6px 8px; }}"
        )

        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Scan SKU")
        self._sku_input.setStyleSheet(input_style)
        form.addWidget(self._sku_input, stretch=2)

        self._qty_input = QSpinBox()
        self._qty_input.setRange(1, 100_000)
        self._qty_input.setValue(1)
        self._qty_input.setStyleSheet(input_style)
        form.addWidget(self._qty_input, stretch=1)

        self._ref_input = QLineEdit()
        self._ref_input.setPlaceholderText(self._spec["ref_placeholder"])
        self._ref_input.setStyleSheet(input_style)
        form.addWidget(self._ref_input, stretch=1)

        self._loc_input = QLineEdit()
        self._loc_input.setPlaceholderText(self._spec["loc_placeholder"])
        self._loc_input.setStyleSheet(input_style)
        form.addWidget(self._loc_input, stretch=1)

        submit = IndustryButton(f"{self._spec['submit_label']} ⏎", variant="primary")
        submit.clicked.connect(self._on_submit)
        self._sku_input.returnPressed.connect(self._on_submit)
        self._ref_input.returnPressed.connect(self._on_submit)
        self._loc_input.returnPressed.connect(self._on_submit)
        form.addWidget(submit)

        widget = QWidget()
        widget.setLayout(form)
        return widget

    def _build_table(self) -> QTableWidget:
        p = INDUSTRY_PALETTE
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Time", "SKU", "Qty", "Ref / bin"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setStyleSheet(
            f"""
            QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; gridline-color: {p['border']}; }}
            QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
                border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
            """
        )
        return table

    def _on_submit(self) -> None:
        barcode = self._sku_input.text().strip().upper()
        quantity = self._qty_input.value()
        ref = self._ref_input.text().strip()
        loc = self._loc_input.text().strip()
        note = " · ".join(part for part in (ref, loc) if part) or None

        if not barcode:
            self._show_error("Scan or enter a SKU first.")
            return

        service = receiving_service if self._direction == "receive" else dispatch_service
        action = service.receive if self._direction == "receive" else service.dispatch
        try:
            action(barcode, quantity, note, location=self._location)
        except ProductNotFoundError:
            self._show_error(f'Unknown SKU "{barcode}". Scan again.')
            return
        except InsufficientStockError as exc:
            self._show_error(f"Only {exc.available} on hand at {self._location.label} for {barcode}. Not logged.")
            return
        except (ValueError, DataAccessError) as exc:
            self._show_error(str(exc))
            return

        self._error_label.hide()
        self._sku_input.clear()
        self._ref_input.clear()
        self._loc_input.clear()
        self._qty_input.setValue(1)
        self._sku_input.setFocus()
        self.reload()
        self.movement_logged.emit()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()

    def reload(self) -> None:
        # This warehouse's own Floor log only - not shipment loading,
        # transfers or counts (those show in the Console / Admin logs).
        movements = [
            m for m in list_recent_movements(limit=80, movement_type=self._direction, location=self._location)
            if m["reason"] in (None, self._direction)
        ][:50]
        total_units = sum(m["quantity"] for m in movements)
        self._summary_label.setText(f"{total_units} units · {len(movements)} {self._spec['unit_noun']}")

        self._table.setRowCount(len(movements))
        for row, movement in enumerate(movements):
            time_text = movement["created_at"].split("T")[-1][:8] if "T" in movement["created_at"] else movement["created_at"]
            self._table.setItem(row, 0, QTableWidgetItem(time_text))
            self._table.setItem(row, 1, QTableWidgetItem(f"{movement['barcode']} · {movement['product_name']}"))
            sign = "+" if movement["movement_type"] == "receive" else "−"
            self._table.setItem(row, 2, QTableWidgetItem(f"{sign}{movement['quantity']}"))
            self._table.setItem(row, 3, QTableWidgetItem(movement["note"] or ""))
