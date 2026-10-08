"""Alerts that slide in from the right edge of a Depot window.

SlideAlerts shows one card per NEW low-stock / out-of-stock event (the feed in
database/activity_repository.py) anywhere in the company, so several products
running low at once each get their own card instead of one replacing the
other. At most MAX_VISIBLE are on screen; the rest wait their turn. Cards fade
away after a few seconds or when clicked. `announce(products, site)` does the
same for what is ALREADY low at a depot (used right after switching depot).
"""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from database import activity_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.theme import INDUSTRY_PALETTE
from shared.activity import describe
from shared.gui_kit.motion import animations_enabled
from shared.i18n import tr
from shared.models import ActivityEvent, Product

MAX_VISIBLE = 4
SHOW_MS = 8000
KINDS = ("stock_low", "stock_out")
_AMBER = "#f4b400"
_RED = "#c0392b"


class _Card(QFrame):
    def __init__(self, host: QWidget, title: str, detail: str, color: str, on_close):
        super().__init__(host)
        p = INDUSTRY_PALETTE
        self.setObjectName("slideAlert")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"#slideAlert {{ background-color: {p['background']}; border: 1.5px solid {p['text_primary']}; "
                           f"border-left: 8px solid {color}; }} QLabel {{ background: transparent; border: none; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(1)
        head = QLabel(title)
        head.setWordWrap(True)
        head.setStyleSheet(f"font-weight: 700; font-size: 14px; color: {p['text_primary']};")
        layout.addWidget(head)
        if detail:
            sub = QLabel(detail)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
            layout.addWidget(sub)
        self.setFixedWidth(360)
        self.adjustSize()
        self._on_close = on_close

    def mousePressEvent(self, event) -> None:
        self._on_close(self)


class SlideAlerts(QObject):
    def __init__(self, window: QWidget, on_open=None):
        """`on_open`: called when a card is clicked (the Console jumps to Shipments)."""
        super().__init__(window)
        self._window = window
        self._on_open = on_open
        self._cursor = 0
        self._cards: list[_Card] = []
        self._waiting: deque[tuple[str, str, str]] = deque()
        self._anims: list[QPropertyAnimation] = []
        self.start()

    def start(self) -> None:
        try:
            self._cursor = activity_repository.latest_id()
        except DATABASE_ERRORS:
            self._cursor = 0

    # --- feeding -----------------------------------------------------------

    def poll(self) -> int:
        """Show cards for low/out events written since the last look; returns how many were new."""
        try:
            events = [e for e in activity_repository.list_since(self._cursor) if e.kind in KINDS]
            self._cursor = max(self._cursor, activity_repository.latest_id())
        except DATABASE_ERRORS:
            return 0
        for event in events:
            self._queue_event(event)
        self._pump()
        return len(events)

    def _queue_event(self, event: ActivityEvent) -> None:
        title, detail = describe(event)
        self._waiting.append((title, detail, _RED if event.kind == "stock_out" else _AMBER))

    def announce(self, products: list[Product], site: str) -> None:
        for product in products:
            title = tr("depot.alert.low_here").format(product=product.name, site=site)
            detail = tr("depot.alert.low_detail").format(left=product.stock_quantity, level=product.critical_stock_level)
            self._waiting.append((title, detail, _RED if product.stock_quantity <= 0 else _AMBER))
        self._pump()

    # --- showing -----------------------------------------------------------

    def _pump(self) -> None:
        while self._waiting and len(self._cards) < MAX_VISIBLE and self._window.isVisible():
            title, detail, color = self._waiting.popleft()
            card = _Card(self._window, title, detail, color, self._clicked)
            self._cards.append(card)
            self._relayout(new=card)
            QTimer.singleShot(SHOW_MS, lambda c=card: self._dismiss(c))

    def _clicked(self, card: _Card) -> None:
        self._dismiss(card)
        if self._on_open is not None:
            self._on_open()

    def _dismiss(self, card: _Card) -> None:
        if card in self._cards:
            self._cards.remove(card)
            card.hide()
            card.deleteLater()
            self._relayout()
            self._pump()

    def active(self) -> list[QWidget]:
        return list(self._cards)

    def _relayout(self, new: _Card | None = None) -> None:
        margin, gap = 18, 8
        y = margin + 70  # clear the header
        x = self._window.width() - 360 - margin
        for card in self._cards:
            card.adjustSize()
            target = QPoint(x, y)
            if card is new:
                card.move(self._window.width(), y)  # starts off-screen on the right...
                card.show()
                card.raise_()
                self._slide(card, target)  # ...and slides in
            else:
                card.move(target)
            y += card.height() + gap

    def _slide(self, card: _Card, target: QPoint) -> None:
        if not animations_enabled():
            card.move(target)
            return
        animation = QPropertyAnimation(card, b"pos", card)
        animation.setDuration(280)
        animation.setStartValue(card.pos())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start()
        self._anims.append(animation)
        animation.finished.connect(lambda a=animation: self._anims.remove(a) if a in self._anims else None)
