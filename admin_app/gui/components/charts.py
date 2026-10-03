"""Small painted charts for the Reports page: a revenue line chart with a
prior-period overlay and a dashed projection, a donut for the breakdown,
and a sparkline. Painted with QPainter rather than built from widgets -
the same call milestone_strip.py makes - and fed plain lists of numbers;
all arithmetic (axis rounding, projection, percentages) lives in
shared/analytics.py, not here.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from admin_app.theme import CLASSICAL_PALETTE
from shared.analytics import axis_ticks, compact_amount

_LEFT = 54  # room for the y-axis labels
_BOTTOM = 24  # room for the x-axis labels
_TOP = 8


def _color(hex_color: str, alpha: int | None = None) -> QColor:
    color = QColor(hex_color)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


class RevenueChart(QWidget):
    """Current period (solid gold), previous period (muted), and an
    optional projection (dashed gold). Hovering emits `hovered(index)`
    (-1 when the pointer leaves) so the page can print that day's figures
    next to the heading, like the mockup's hover readout."""

    hovered = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._current: list[float] = []
        self._previous: list[float] = []
        self._projection: list[float] = []
        self._x_labels: list[tuple[int, str]] = []
        self._points = 0
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    def set_series(
        self,
        current: list[float],
        previous: list[float],
        projection: list[float],
        x_labels: list[tuple[int, str]],
        total_points: int,
    ) -> None:
        """`projection` continues right after `current` (its first value is
        for the day after the last real one). `x_labels` are (index, text)
        pairs; `total_points` is the x-range width (>= every series)."""
        self._current, self._previous, self._projection = current, previous, projection
        self._x_labels = x_labels
        self._points = max(total_points, len(current) + len(projection), len(previous), 1)
        self._hover = -1
        self.update()

    # --- geometry -----------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(_LEFT, _TOP, max(self.width() - _LEFT - 8, 1), max(self.height() - _TOP - _BOTTOM, 1))

    def _x(self, index: int) -> float:
        plot = self._plot()
        if self._points <= 1:
            return plot.left()
        return plot.left() + plot.width() * index / (self._points - 1)

    def _maximum(self) -> float:
        values = self._current + self._previous + self._projection
        return max(values) if values else 0.0

    def _y(self, value: float, top: float) -> float:
        plot = self._plot()
        return plot.bottom() - plot.height() * (value / top if top else 0)

    def _path(self, values: list[float], offset: int, top: float) -> QPainterPath:
        path = QPainterPath()
        for i, value in enumerate(values):
            point = QPointF(self._x(i + offset), self._y(value, top))
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        return path

    # --- events -------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        plot = self._plot()
        if self._points <= 1 or plot.width() <= 0:
            return
        fraction = (event.position().x() - plot.left()) / plot.width()
        index = max(0, min(self._points - 1, round(fraction * (self._points - 1))))
        if index != self._hover:
            self._hover = index
            self.hovered.emit(index)
            self.update()

    def leaveEvent(self, event) -> None:
        if self._hover != -1:
            self._hover = -1
            self.hovered.emit(-1)
            self.update()

    # --- painting -----------------------------------------------------

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        plot = self._plot()
        top, ticks = axis_ticks(self._maximum())

        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)

        for tick in ticks:
            y = self._y(tick, top)
            painter.setPen(QPen(_color(p["border"]), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(_color(p["text_secondary"]))
            painter.drawText(QRectF(0, y - 8, _LEFT - 8, 16), Qt.AlignRight | Qt.AlignVCenter, compact_amount(tick))

        painter.setPen(_color(p["text_secondary"]))
        for index, text in self._x_labels:
            x = self._x(index)
            painter.drawText(QRectF(x - 30, plot.bottom() + 4, 60, 16), Qt.AlignCenter, text)

        if self._previous:
            painter.setPen(QPen(_color(p["text_secondary"], 140), 1.5))
            painter.drawPath(self._path(self._previous, 0, top))
        if self._projection and self._current:
            anchor = [self._current[-1]] + self._projection
            pen = QPen(_color(p["accent"], 170), 1.5, Qt.DashLine)
            painter.setPen(pen)
            painter.drawPath(self._path(anchor, len(self._current) - 1, top))
        if self._current:
            painter.setPen(QPen(_color(p["accent"]), 2))
            painter.drawPath(self._path(self._current, 0, top))

        if 0 <= self._hover < self._points:
            x = self._x(self._hover)
            painter.setPen(QPen(_color(p["text_secondary"], 120), 1, Qt.DotLine))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            if self._hover < len(self._current):
                painter.setPen(Qt.NoPen)
                painter.setBrush(_color(p["accent"]))
                painter.drawEllipse(QPointF(x, self._y(self._current[self._hover], top)), 4, 4)
        painter.end()


class DonutChart(QWidget):
    """A ring split into coloured arcs with a centre label. Hovering a
    segment is the page's job (it highlights the matching legend row and
    calls `set_highlight`); this widget only draws."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._segments: list[tuple[str, float, str]] = []
        self._highlight = -1
        self._center_top = ""
        self._center_value = ""
        self._center_sub = ""
        self.setMinimumSize(180, 180)

    def set_segments(self, segments: list[tuple[str, float, str]]) -> None:
        self._segments = segments
        self.update()

    def set_highlight(self, index: int) -> None:
        self._highlight = index
        self.update()

    def set_center(self, top: str, value: str, sub: str = "") -> None:
        self._center_top, self._center_value, self._center_sub = top, value, sub
        self.update()

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 24
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        total = sum(value for _, value, _ in self._segments)

        if total <= 0:
            painter.setPen(QPen(_color(p["border"]), 14))
            painter.drawEllipse(rect)
        else:
            start = 90 * 16  # 12 o'clock
            for index, (_, value, color) in enumerate(self._segments):
                if value <= 0:
                    continue
                span = -int(round(360 * 16 * value / total))
                dimmed = self._highlight != -1 and index != self._highlight
                pen = QPen(_color(color, 110 if dimmed else 255), 20 if index == self._highlight else 14)
                pen.setCapStyle(Qt.FlatCap)
                painter.setPen(pen)
                painter.drawArc(rect, start, span)
                start += span

        painter.setPen(_color(p["text_secondary"]))
        small = QFont()
        small.setPixelSize(10)
        painter.setFont(small)
        painter.drawText(QRectF(rect.left(), rect.center().y() - 30, rect.width(), 14), Qt.AlignCenter, self._center_top.upper())
        big = QFont()
        big.setPixelSize(24)
        painter.setFont(big)
        painter.setPen(_color(p["text_primary"]))
        painter.drawText(QRectF(rect.left(), rect.center().y() - 14, rect.width(), 30), Qt.AlignCenter, self._center_value)
        painter.setFont(small)
        painter.setPen(_color(p["text_secondary"]))
        painter.drawText(QRectF(rect.left(), rect.center().y() + 16, rect.width(), 14), Qt.AlignCenter, self._center_sub)
        painter.end()


class Sparkline(QWidget):
    """A 7-point trend line; gold when the trend is up (or unknown), muted
    when it is down - the mockup's rule."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._values: list[float] = []
        self._up = True
        self.setFixedSize(96, 28)

    def set_values(self, values: list[float], up: bool = True) -> None:
        self._values, self._up = values, up
        self.update()

    def paintEvent(self, event) -> None:
        if len(self._values) < 2:
            return
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        low, high = min(self._values), max(self._values)
        span = (high - low) or 1.0
        width, height = self.width() - 4, self.height() - 4
        path = QPainterPath()
        for i, value in enumerate(self._values):
            point = QPointF(2 + width * i / (len(self._values) - 1), 2 + height - height * (value - low) / span)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        color = _color(p["accent"] if self._up else "#7d7979")
        painter.setPen(QPen(color, 1.5))
        painter.drawPath(path)
        painter.end()
