"""The Industry theme's one signature visual motif: a square-cornered
bordered surface with four small "blueprint" registration-mark ticks at
its corners - drawn from the mockup's `<i class="corner tl/tr/bl/br">`
elements (four thin cross-hair L-brackets, inset from each corner,
`color-mix(in srgb, var(--color-text) 55%, transparent)`).

Qt's QSS has no `::before`/`::after` pseudo-elements, so this is a real
paintEvent rather than a stylesheet trick - the one place in depot_app
that needs custom painting instead of QSS. Every card/panel/button that
should carry the motif wraps its content in (or subclasses) BlueprintFrame
rather than a plain QFrame.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame

_TICK_ARM_PX = 9
_TICK_INSET_PX = 5


class BlueprintFrame(QFrame):
    """A QFrame that paints corner registration-mark ticks after its
    normal QSS-driven background/border. Pass `tick_color` as a plain hex
    string - alpha is applied here (55%, matching the mockup's
    color-mix), so callers pass a solid color, not an rgba() string.
    """

    def __init__(self, tick_color: str, parent=None):
        super().__init__(parent)
        color = QColor(tick_color)
        color.setAlpha(140)  # ~55% of 255
        self._tick_color = color

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pen = QPen(self._tick_color)
        pen.setWidthF(1.2)
        painter.setPen(pen)

        w = self.width()
        h = self.height()
        i = _TICK_INSET_PX
        a = _TICK_ARM_PX

        corners = [
            # (corner point, horizontal arm end, vertical arm end)
            (QPointF(i, i), QPointF(i + a, i), QPointF(i, i + a)),  # top-left
            (QPointF(w - i, i), QPointF(w - i - a, i), QPointF(w - i, i + a)),  # top-right
            (QPointF(i, h - i), QPointF(i + a, h - i), QPointF(i, h - i - a)),  # bottom-left
            (QPointF(w - i, h - i), QPointF(w - i - a, h - i), QPointF(w - i, h - i - a)),  # bottom-right
        ]
        for corner, h_end, v_end in corners:
            painter.drawLine(corner, h_end)
            painter.drawLine(corner, v_end)
        painter.end()
