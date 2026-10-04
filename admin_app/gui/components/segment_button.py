"""Checkable tab/segment button in the mockup's style: muted text, gold
text + gold underline when selected. The underline grows out from the
centre and the text warms up when a tab is picked."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QPushButton

from admin_app.gui.motion import Level, blend
from admin_app.theme import CLASSICAL_PALETTE


class SegmentButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label.replace("&", "&&"), parent)  # "&" alone is a mnemonic marker
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 6px 10px; font-size: 13px; }"
        )
        self._on = Level(self, lambda _v: self.update(), 200, 1.0 if self.isChecked() else 0.0)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self.toggled.connect(lambda on: self._on.go(1.0 if on else 0.0))

    def setChecked(self, on: bool) -> None:  # programmatic selection jumps; clicks animate
        super().setChecked(on)
        self._on.go(1.0 if on else 0.0, animate=False)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        idle = blend(p["text_secondary"], p["text_primary"], self._hover.value)
        colour = QColor(blend(idle, p["accent"], self._on.value) if self.isEnabled() else p["text_secondary"])
        painter.setPen(colour)
        painter.setFont(self.font())
        text = self.text().replace("&&", "&")
        rect = self.rect().adjusted(10, 0, -10, -2)
        painter.drawText(rect, Qt.AlignCenter, QFontMetrics(self.font()).elidedText(text, Qt.ElideRight, rect.width()))
        if self._on.value > 0.01:
            width = self.width() * self._on.value
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(p["accent"]))
            painter.drawRect(QRectF((self.width() - width) / 2, self.height() - 2, width, 2))
