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

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from database import settings_repository
from database.stock_repository import critical_at
from depot_app.gui.icons import TRIANGLE_ALERT
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.constants import CRITICAL_STOCK_POLL_INTERVAL_MS
from shared.gui_kit.icon_kit import svg_to_icon
from shared.gui_kit.motion import animations_enabled, fade_in
from shared.gui_kit.polling import PollingTimer
from shared.i18n import plural, tr
from shared.models import UNASSIGNED, Product, StockLocation

_AMBER = "#f4b400"
_AMBER_SOFT = "#ffd24d"


class _HazardStripe(QWidget):
    """The mockup's diagonal black/amber warning stripe down the banner's left edge."""

    def __init__(self):
        super().__init__()
        self.setFixedWidth(18)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(_AMBER))
        painter.setClipRect(self.rect())
        pen = QPen(QColor(INDUSTRY_PALETTE["text_primary"]))
        pen.setWidth(8)
        painter.setPen(pen)
        step, height = 16, self.height()
        y = -step
        while y < height + step:
            painter.drawLine(-4, y + 18, 22, y - 8)  # one -45deg band per 16px
            y += step


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

        self._card = QFrame()
        self._card.setObjectName("lowStockCard")
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        self._card.setStyleSheet(
            f"#lowStockCard {{ background-color: {_AMBER}; border: none; border-bottom: 2px solid {p['text_primary']}; }}"
        )
        frame = QHBoxLayout(self._card)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        frame.addWidget(_HazardStripe())

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        card_layout = QVBoxLayout(content)
        card_layout.setContentsMargins(20, 12, 20, 12)
        card_layout.setSpacing(8)
        frame.addWidget(content, stretch=1)

        header = QHBoxLayout()
        header.setSpacing(10)
        self._pulse = QWidget()
        self._pulse.setFixedSize(12, 12)
        self._pulse.setStyleSheet(f"background-color: {p['text_primary']};")
        header.addWidget(self._pulse)
        icon_label = QLabel()
        icon_label.setPixmap(svg_to_icon(TRIANGLE_ALERT, p["text_primary"], size=28).pixmap(28, 28))
        header.addWidget(icon_label)
        self._heading = QLabel()
        self._heading.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; text-transform: uppercase; "
            f"letter-spacing: 1px; font-size: 26px; color: {p['text_primary']};"
        )
        header.addWidget(self._heading)
        header.addStretch(1)
        note = QLabel(tr("depot.banner.reorder_note"))
        note.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {p['text_primary']};")
        header.addWidget(note)
        card_layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(66)
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
        self._start_pulse()

        self._poller = PollingTimer(lambda: critical_at(self._location), CRITICAL_STOCK_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._render)
        self._poller.start()

    def _start_pulse(self) -> None:
        """The little square beats like the mockup's `wmsPulse` (1.2 s, 100% to 25%)."""
        if not animations_enabled():
            return
        effect = QGraphicsOpacityEffect(self._pulse)
        self._pulse.setGraphicsEffect(effect)
        beat = QPropertyAnimation(effect, b"opacity", self._pulse)
        beat.setDuration(1200)
        beat.setKeyValueAt(0.0, 1.0)
        beat.setKeyValueAt(0.5, 0.25)
        beat.setKeyValueAt(1.0, 1.0)
        beat.setEasingCurve(QEasingCurve.InOutSine)
        beat.setLoopCount(-1)
        beat.start()
        self._pulse._beat = beat

    def reload(self) -> None:
        """Force an immediate refresh outside the poll interval (e.g. on tab focus)."""
        self._render(critical_at(self._location))

    def _render(self, products: list[Product]) -> None:
        while self._pills_layout.count() > 1:
            item = self._pills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not products or not settings_repository.safe_notifications().low_stock_alerts:
            self._card.hide()
            return

        newly_shown = not self._card.isVisible()
        self._card.show()
        if newly_shown:
            fade_in(self._card)
        self._heading.setText(plural("depot.banner.alert", len(products)))
        for index, product in enumerate(products):
            self._pills_layout.insertWidget(index, self._build_pill(product))

    def _build_pill(self, product: Product) -> QWidget:
        p = INDUSTRY_PALETTE
        pill = QWidget()
        pill.setObjectName("lowStockPill")
        pill.setAttribute(Qt.WA_StyledBackground, True)
        pill.setStyleSheet(
            f"#lowStockPill {{ background-color: {_AMBER_SOFT}; border: 1.5px solid {p['text_primary']}; }}"
        )
        layout = QHBoxLayout(pill)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        qty = QLabel(str(product.stock_quantity))
        qty.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; color: {p['text_primary']}; "
            f"background: transparent; border: none;"
        )
        layout.addWidget(qty)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        name_label = QLabel(f"{product.barcode} \u00b7 {product.name}")
        name_label.setStyleSheet(
            f"font-weight: 700; font-size: 14px; color: {p['text_primary']}; background: transparent; border: none;"
        )
        below_label = QLabel(tr("depot.banner.left_of_min").format(minimum=product.critical_stock_level))
        below_label.setStyleSheet(
            f"font-size: 12px; color: {p['text_primary']}; background: transparent; border: none;"
        )
        texts.addWidget(name_label)
        texts.addWidget(below_label)
        layout.addLayout(texts)

        return pill
