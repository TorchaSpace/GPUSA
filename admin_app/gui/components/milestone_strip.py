"""The Treasury mockup's "Next 30 days · Payment & collection milestones"
strip: one column per day (weekday + date, today and each 1st marked in
gold, weekends dimmed), a green circle per collection and a smaller
orange one per payment (sized by amount, filled when overdue), and a
line across the top tracking the running net of scheduled items.
Hovering a day emits `day_hovered(index)` (-1 on leave) so the page can
list that day's documents underneath, like the mockup's focus row.

Painted rather than built from widgets - 30 columns x N dots is exactly
the kind of dense, data-driven graphic QPainter is for. Data comes from
shared.treasury.milestones(); see its docstring for why the line is a
"net of scheduled items" starting at zero rather than the mockup's
projected cash balance.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from admin_app.theme import CLASSICAL_PALETTE
from shared.treasury import DayMilestones

_LINE_BAND = 40  # px reserved at the top for the running-net line
_HEADER = 34  # weekday + date labels


def _dot_size(amount: float, scale: float = 1.0) -> float:
    """The mockup's sizing: 6 + sqrt(amount / 1000), clamped to 8..16 px."""
    return max(8.0, min(16.0, 6 + math.sqrt(max(amount, 0) / 1000))) * scale


class MilestoneStrip(QWidget):
    day_hovered = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._days: list[DayMilestones] = []
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(170)

    def set_days(self, days: list[DayMilestones]) -> None:
        self._days = days
        self._hover = -1
        self.update()

    def days(self) -> list[DayMilestones]:
        return self._days

    # --- geometry -----------------------------------------------------

    def _column_width(self) -> float:
        return self.width() / max(len(self._days), 1)

    def _index_at(self, x: float) -> int:
        if not self._days:
            return -1
        index = int(x // self._column_width())
        return index if 0 <= index < len(self._days) else -1

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()
            self.day_hovered.emit(index)

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.update()
            self.day_hovered.emit(-1)

    # --- painting -----------------------------------------------------

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._days:
            return
        width = self._column_width()
        top = _LINE_BAND
        bottom = self.height()
        green, orange, gold = QColor(p["alert_success"]), QColor(p["alert_warning"]), QColor(p["accent"])
        faint = QColor(p["text_primary"])
        faint.setAlpha(15)

        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() * 0.8, 7))
        painter.setFont(small)

        for index, day in enumerate(self._days):
            x = index * width
            column = QRectF(x, top, width, bottom - top)
            weekend = day.day.weekday() >= 5
            if index == self._hover:
                shade = QColor(p["text_primary"])
                shade.setAlpha(13)
                painter.fillRect(column, shade)
            elif index == 0:
                shade = QColor(gold)
                shade.setAlpha(18)
                painter.fillRect(column, shade)
            elif weekend:
                painter.fillRect(column, QColor(234, 231, 231, 5))
            if index > 0:
                rule = QColor(gold) if day.day.day == 1 else faint
                if day.day.day == 1:
                    rule.setAlpha(115)
                painter.setPen(QPen(rule))
                painter.drawLine(QPointF(x, top), QPointF(x, bottom))

            label_color = gold if index == 0 or day.day.day == 1 else (
                QColor("#7d7979") if weekend else QColor("#d7d3d3")
            )
            painter.setPen(QPen(QColor(p["text_secondary"])))
            painter.drawText(QRectF(x, top + 2, width, 14), Qt.AlignCenter, day.day.strftime("%a")[:2].upper())
            painter.setPen(QPen(label_color))
            number = day.day.strftime("%b") if day.day.day == 1 else str(day.day.day)
            painter.drawText(QRectF(x, top + 16, width, 16), Qt.AlignCenter, number)

            y = top + _HEADER + 6
            center = x + width / 2
            for entries, color, scale in ((day.incoming, green, 1.0), (day.outgoing, orange, 0.8)):
                for entry in entries:
                    size = min(_dot_size(entry.amount, scale), width - 4)
                    overdue = entry.due_date < day.day
                    painter.setPen(QPen(color, 1.5))
                    fill = QColor(color)
                    fill.setAlpha(90 if overdue else 0)
                    painter.setBrush(fill)
                    painter.drawEllipse(QRectF(center - size / 2, y, size, size))
                    y += size + 3
                    if y > bottom - 8:
                        break
            painter.setBrush(Qt.NoBrush)

        # Running net line across the top band.
        values = [0.0] + [d.cumulative_net for d in self._days]
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        path = QPainterPath()
        for i, value in enumerate(values):
            px = min(i, len(self._days)) / len(self._days) * self.width()
            py = 6 + (1 - (value - low) / span) * (_LINE_BAND - 14)
            if i == 0:
                path.moveTo(px, py)
            else:
                path.lineTo(px, py)
        line_color = QColor(gold)
        line_color.setAlpha(200)
        painter.setPen(QPen(line_color, 1.5))
        painter.drawPath(path)
