"""Depot's primary responsibility, made visible: a full-width amber alert
banner listing every product at or below its critical_stock_level, kept
current via shared.gui_kit.polling.PollingTimer (SQLite has no
cross-process push - see that module's docstring). Recreates the
mockup's Floor App "Low Stock Alerts" banner - hidden entirely when
nothing is low, one pill per flagged product when something is.

This is the concrete answer to the architecture decision that Depot (not
Admin) owns making a low-stock condition actually get seen - see
architecture.md. Contrast with admin_app/gui/pages/overview_page.py's
"below reorder only" filter, which shows the same underlying data but on
demand, not kept live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from database.stock_repository import critical_at
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.icons import TRIANGLE_ALERT
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.constants import CRITICAL_STOCK_POLL_INTERVAL_MS
from shared.gui_kit.icon_kit import svg_to_icon
from shared.gui_kit.polling import PollingTimer
from shared.models import UNASSIGNED, Product, StockLocation

_AMBER = "#f4b400"
_AMBER_PALE = "#fff6d6"


class LowStockBanner(QWidget):
    """Products at/below their critical level AT `location` (this depot's
    warehouse) - stock elsewhere in the company doesn't refill this floor."""

    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._location = location
        p = INDUSTRY_PALETTE
        self.setStyleSheet("background: transparent;")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._card = BlueprintFrame(tick_color=p["text_primary"])
        self._card.setStyleSheet(f"background-color: {_AMBER_PALE}; border: 1px solid {_AMBER};")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(20, 14, 20, 14)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setPixmap(svg_to_icon(TRIANGLE_ALERT, p["text_primary"], size=22).pixmap(22, 22))
        header.addWidget(icon_label)
        self._heading = QLabel()
        self._heading.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; text-transform: uppercase; "
            f"letter-spacing: 1px; font-size: 15px; color: {p['text_primary']};"
        )
        header.addWidget(self._heading)
        header.addStretch(1)
        note = QLabel("Flagged for reorder review")
        note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        header.addWidget(note)
        card_layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(64)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        pills_host = QWidget()
        pills_host.setStyleSheet("background: transparent;")
        self._pills_layout = QHBoxLayout(pills_host)
        self._pills_layout.setContentsMargins(0, 0, 0, 0)
        self._pills_layout.setSpacing(10)
        self._pills_layout.addStretch(1)
        scroll.setWidget(pills_host)
        card_layout.addWidget(scroll)

        outer.addWidget(self._card)
        self._card.hide()

        self._poller = PollingTimer(lambda: critical_at(self._location), CRITICAL_STOCK_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._render)
        self._poller.start()

    def reload(self) -> None:
        """Force an immediate refresh outside the poll interval (e.g. on tab focus)."""
        self._render(critical_at(self._location))

    def _render(self, products: list[Product]) -> None:
        while self._pills_layout.count() > 1:
            item = self._pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not products:
            self._card.hide()
            return

        self._card.show()
        self._heading.setText(f"{len(products)} Low Stock Alert{'s' if len(products) != 1 else ''}")
        for index, product in enumerate(products):
            self._pills_layout.insertWidget(index, self._build_pill(product))

    def _build_pill(self, product: Product) -> QWidget:
        p = INDUSTRY_PALETTE
        pill = QWidget()
        pill.setStyleSheet(f"background-color: #ffffff; border: 1px solid {_AMBER};")
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        qty = QLabel(str(product.stock_quantity))
        qty.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(qty)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        name_label = QLabel(f"{product.barcode} · {product.name}")
        name_label.setStyleSheet(f"font-weight: 700; font-size: 12px; color: {p['text_primary']};")
        below_label = QLabel(f"left of min {product.critical_stock_level}")
        below_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        texts.addWidget(name_label)
        texts.addWidget(below_label)
        texts_widget = QWidget()
        texts_widget.setStyleSheet("background: transparent;")
        texts_widget.setLayout(texts)
        layout.addWidget(texts_widget)

        return pill
