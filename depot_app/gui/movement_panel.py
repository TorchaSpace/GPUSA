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

from PySide6.QtCore import QEasingCurve, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.exceptions import DATABASE_ERRORS, InsufficientStockError, ProductInactiveError, ProductNotFoundError
from database.inventory_repository import list_recent_movements
from shared.models import UNASSIGNED, StockLocation
from shared.warehousing import tr_or
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.floor_style import floor_table, input_style, labelled, notice_style
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.services import dispatch_service, receiving_service
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.gui_kit.motion import animations_enabled, blend, fade_in, toast

_DIRECTIONS = {
    "receive": {
        "glyph": "↓",
        "title": "Inbound",
        "unit_noun": "receipts",
        "ref_label": "PO / ASN ref",
        "ref_col": "Ref",
        "loc_col": "Bin",
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
        "ref_col": "Order",
        "loc_col": "Dock",
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
        card.setObjectName("movementCard")
        bar = p["accent"] if direction == "receive" else p["text_primary"]
        card.setStyleSheet(
            f"#movementCard {{ background-color: {p['surface']}; border: 1px solid {p['border']}; "
            f"border-top: 4px solid {bar}; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        glyph = QLabel(self._spec["glyph"])
        glyph.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 30px; font-weight: 600; color: {bar};")
        header.addWidget(glyph)
        title = QLabel(self._spec["title"].upper())
        title.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; letter-spacing: 1px; "
            f"font-size: 28px; color: {p['text_primary']};"
        )
        header.addWidget(title)
        header.addStretch(1)
        self._summary_label = QLabel()
        self._summary_label.setTextFormat(Qt.RichText)
        self._summary_label.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        header.addWidget(self._summary_label)
        card_layout.addLayout(header)

        card_layout.addWidget(self._build_form(), stretch=0)

        self._table = self._build_table()
        card_layout.addWidget(self._table, stretch=1)

        outer.addWidget(card)
        self.reload()

    def _build_form(self) -> QWidget:
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 1)

        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Scan or type")
        self._sku_input.setMinimumHeight(58)
        self._sku_input.setStyleSheet(input_style(24))
        grid.addWidget(labelled("SKU / barcode", self._sku_input), 0, 0)

        self._qty_input = QSpinBox()
        self._qty_input.setRange(1, 100_000)
        self._qty_input.setValue(1)
        self._qty_input.setMinimumHeight(58)
        self._qty_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._qty_input.setStyleSheet(input_style(24))
        grid.addWidget(labelled("Qty", self._qty_input), 0, 1)

        self._ref_input = QLineEdit()
        self._ref_input.setPlaceholderText(self._spec["ref_placeholder"])
        self._ref_input.setMinimumHeight(48)
        self._ref_input.setStyleSheet(input_style(18))
        grid.addWidget(labelled(self._spec["ref_label"], self._ref_input), 1, 0)

        self._loc_input = QLineEdit()
        self._loc_input.setPlaceholderText(self._spec["loc_placeholder"])
        self._loc_input.setMinimumHeight(48)
        self._loc_input.setStyleSheet(input_style(18))
        grid.addWidget(labelled(self._spec["loc_label"], self._loc_input), 1, 1)

        self._error_label = QLabel()
        self._error_label.setStyleSheet(notice_style())
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        grid.addWidget(self._error_label, 2, 0, 1, 2)

        submit = IndustryButton(f"{self._spec['submit_label']} \u23ce", variant="primary", height=60, font_px=20)
        submit.clicked.connect(self._on_submit)
        self._submit_button = submit
        self._sku_input.returnPressed.connect(self._on_submit)
        self._ref_input.returnPressed.connect(self._on_submit)
        self._loc_input.returnPressed.connect(self._on_submit)
        grid.addWidget(submit, 3, 0, 1, 2)

        widget = QWidget()
        widget.setStyleSheet("background: transparent;")
        widget.setLayout(grid)
        return widget

    def _build_table(self) -> QTableWidget:
        table = floor_table(["Time", "SKU", "Qty", self._spec["ref_col"], self._spec["loc_col"]], stretch=(1,))
        table.setMinimumHeight(200)
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
        except ProductInactiveError:
            self._show_error(tr_or("depot.sku_inactive", 'SKU "{sku}" is deactivated - it can\'t be received. Reactivate it in Admin first.').format(sku=barcode))
            return
        except ProductNotFoundError:
            self._show_error(f'Unknown SKU "{barcode}". Scan again.')
            return
        except InsufficientStockError as exc:
            self._show_error(f"Only {exc.available} on hand at {self._location.label} for {barcode}. Not logged.")
            return
        except (ValueError, *DATABASE_ERRORS) as exc:  # e.g. over capacity, inactive warehouse, bad quantity
            self._show_error(str(exc))
            return

        self._error_label.hide()
        self._sku_input.clear()
        self._ref_input.clear()
        self._loc_input.clear()
        self._qty_input.setValue(1)
        self._sku_input.setFocus()
        self.reload()
        self._flash_newest_row()
        toast(self, f"{self._spec['submit_label']}: {quantity} \u00d7 {barcode}")
        self.movement_logged.emit()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()
        fade_in(self._error_label)

    def _flash_newest_row(self) -> None:
        """The row just logged lights up in steel-blue and settles back."""
        if not animations_enabled() or self._table.rowCount() == 0:
            return
        p = INDUSTRY_PALETTE
        table = self._table
        start, end = p["accent_100"], p["surface"]
        animation = QVariantAnimation(table)
        animation.setDuration(1100)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        def paint(level) -> None:
            if table.rowCount() == 0:
                return
            brush = QBrush(QColor(blend(end, start, float(level))))
            for column in range(table.columnCount()):
                cell = table.item(0, column)
                if cell is not None:
                    cell.setBackground(brush)

        def done() -> None:
            for column in range(table.columnCount()):
                cell = table.item(0, column) if table.rowCount() else None
                if cell is not None:
                    cell.setBackground(QBrush())

        animation.valueChanged.connect(paint)
        animation.finished.connect(done)
        table._flash = animation
        animation.start()

    def reload(self) -> None:
        # This warehouse's own Floor log only - not shipment loading,
        # transfers or counts (those show in the Console / Admin logs).
        try:
            movements = [
                m for m in list_recent_movements(limit=80, movement_type=self._direction, location=self._location)
                if m["reason"] in (None, self._direction)
            ][:50]
        except DATABASE_ERRORS as exc:  # a locked database on a refresh must not crash the Floor
            self._show_error(f"Couldn't load the log: {exc}")
            return
        total_units = sum(m["quantity"] for m in movements)
        p = INDUSTRY_PALETTE
        self._summary_label.setText(
            f"<b style='font-size:18px; color:{p['text_primary']}'>{total_units}</b> units \u00b7 "
            f"{len(movements)} {self._spec['unit_noun']}"
        )

        self._table.setRowCount(len(movements))
        sign = "+" if self._direction == "receive" else "\u2212"
        qty_color = QColor(p["accent_900"] if self._direction == "receive" else p["text_primary"])
        for row, movement in enumerate(movements):
            time_text = movement["created_at"].split("T")[-1][:8] if "T" in movement["created_at"] else movement["created_at"]
            when = QTableWidgetItem(time_text)
            when.setForeground(QColor(p["text_secondary"]))
            self._table.setItem(row, 0, when)
            sku = QTableWidgetItem(f"{movement['barcode']}  \u00b7  {movement['product_name']}")
            sku.setFont(self._bold())
            self._table.setItem(row, 1, sku)
            qty = QTableWidgetItem(f"{sign}{movement['quantity']}")
            qty.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            qty.setForeground(qty_color)
            big = self._bold(19)
            qty.setFont(big)
            self._table.setItem(row, 2, qty)
            parts = (movement["note"] or "").split(" \u00b7 ", 1)
            self._table.setItem(row, 3, QTableWidgetItem(parts[0]))
            self._table.setItem(row, 4, QTableWidgetItem(parts[1] if len(parts) > 1 else ""))

    @staticmethod
    def _bold(size: int | None = None) -> QFont:
        font = QFont()
        font.setBold(True)
        if size:
            font.setPixelSize(size)
        return font
