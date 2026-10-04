"""Check box whose box warms up under the mouse and whose tick draws itself."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton

from admin_app.gui.motion import Level, blend
from admin_app.theme import CLASSICAL_PALETTE

_BOX = 16


class AnimatedCheckBox(QCheckBox):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        # The app-wide stylesheet draws a plain box; ours is painted below.
        self.setStyleSheet(
            f"QCheckBox::indicator {{ width: {_BOX}px; height: {_BOX}px; border: none; background: transparent; }}"
        )
        self._checked = Level(self, lambda _v: self.update(), 180, 1.0 if self.isChecked() else 0.0)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self.toggled.connect(lambda on: self._checked.go(1.0 if on else 0.0))

    def setChecked(self, on: bool) -> None:  # programmatic changes jump, only clicks animate
        super().setChecked(on)
        self._checked.go(1.0 if on else 0.0, animate=False)

    def event(self, event) -> bool:
        if event.type() == QEvent.HoverEnter or event.type() == QEvent.Enter:
            self._hover.go(1.0)
        elif event.type() == QEvent.HoverLeave or event.type() == QEvent.Leave:
            self._hover.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        p = CLASSICAL_PALETTE
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        style = self.style()
        label = QStyleOptionButton(option)
        label.rect = style.subElementRect(QStyle.SE_CheckBoxContents, option, self)
        style.drawControl(QStyle.CE_CheckBoxLabel, label, painter, self)

        indicator = style.subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        box = QRectF(indicator).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        level = self._checked.value
        warm = max(level, self._hover.value if enabled else 0.0)
        edge = QColor(blend("#a39d95", p["accent"], warm) if enabled else p["border"])
        painter.setPen(QPen(edge, 1.2))
        fill = QColor(p["accent"])
        fill.setAlphaF(level if enabled else level * 0.35)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 3.5, 3.5)

        if level > 0.01:
            a = QPointF(box.left() + box.width() * 0.24, box.top() + box.height() * 0.54)
            b = QPointF(box.left() + box.width() * 0.43, box.top() + box.height() * 0.72)
            c = QPointF(box.left() + box.width() * 0.77, box.top() + box.height() * 0.30)
            painter.setPen(QPen(QColor(p["background"]), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            first = min(1.0, level / 0.45)
            painter.drawLine(a, a + (b - a) * first)
            if level > 0.45:
                second = (level - 0.45) / 0.55
                painter.drawLine(b, b + (c - b) * second)
