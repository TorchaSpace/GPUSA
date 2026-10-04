"""The Console's sidebar row: hover tint, then a steel-blue bar that grows
when the page becomes current - painted here so it can animate."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QPushButton

from depot_app.theme import INDUSTRY_PALETTE
from shared.gui_kit.motion import Level, blend


class ConsoleNavButton(QPushButton):
    def __init__(self, text: str):
        super().__init__(text)
        self.setCheckable(True)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self._on = Level(self, lambda _v: self.update(), 220, 0.0)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self.toggled.connect(lambda on: self._on.go(1.0 if on else 0.0))

    def event(self, event) -> bool:
        if event.type() in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        p = INDUSTRY_PALETTE
        painter = QPainter(self)
        on, hover = self._on.value, self._hover.value
        tint = QColor(p["accent"])
        tint.setAlpha(round(255 * 0.10 * on + 255 * 0.06 * hover * (1 - on)))
        painter.fillRect(self.rect(), tint)
        if on > 0.01:
            height = self.height() * on
            painter.fillRect(QRectF(0, (self.height() - height) / 2, 3, height), QColor(p["accent"]))
        colour = QColor(blend(blend(p["text_secondary"], p["text_primary"], hover), p["accent_900"], on))
        shift = 2 * on + hover * (1 - on)
        painter.setPen(colour)
        font = self.font()
        font.setPixelSize(13)
        font.setBold(on > 0.5)
        painter.setFont(font)
        painter.drawText(
            QRectF(12 + shift, 0, self.width() - 18, self.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            self.text().replace("&&", "&"),
        )
