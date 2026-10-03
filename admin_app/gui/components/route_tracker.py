"""The Distribution mockup's "Route tracker · Active routes, warehouse to
dealership": one row per shipment - origin + tracking number on the
left, a progress bar in the middle (covered in the status colour,
remaining dashed, a dot where it is now, the percentage), destination +
ETA on the right. Clicking a row selects it (`route_clicked`), and the
selected row is highlighted with the mockup's gold left edge.

Progress is shared.distribution.progress() - estimated from departure
and ETA, not a live position (there's no carrier feed); the page says
so. A shipment that hasn't left yet shows an empty track and "not
departed". The mockup's intermediate stop dots are dropped: shipments
here are single-drop, warehouse to one dealership.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from admin_app.theme import CLASSICAL_PALETTE
from shared.distribution import eta_text, live_status, progress
from shared.formatting import local_time_text
from shared.models import Shipment

GOLD = "#e1ad66"
WARN = "#df9460"
ARRIVING = "#eae7e7"

STATUS_COLORS = {"In Transit": GOLD, "Arriving": ARRIVING, "Delayed": WARN, "Scheduled": "#9b9797"}


class RouteBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction: float | None = None
        self._color = QColor(GOLD)
        self.setMinimumHeight(34)
        self.setMinimumWidth(160)

    def set_state(self, fraction: float | None, color: str) -> None:
        self._fraction = fraction
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        y = self.height() / 2
        left, right = 8.0, self.width() - 48.0
        dash = QPen(QColor(p["border"]), 2, Qt.DashLine)
        painter.setPen(dash)
        painter.drawLine(QPointF(left, y), QPointF(right, y))
        painter.setBrush(QColor(p["background"]))
        painter.setPen(QPen(QColor(p["text_secondary"]), 1.5))
        painter.drawEllipse(QPointF(left, y), 4, 4)
        painter.drawEllipse(QPointF(right, y), 4, 4)
        if self._fraction is None:
            painter.setPen(QPen(QColor(p["text_secondary"])))
            painter.drawText(QRectF(right + 6, 0, 44, self.height()), Qt.AlignVCenter | Qt.AlignLeft, "—")
            painter.drawText(QRectF(left, 0, right - left, y - 4), Qt.AlignCenter | Qt.AlignBottom, "not departed")
            return
        x = left + (right - left) * self._fraction
        painter.setPen(QPen(self._color, 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(left, y), QPointF(x, y))
        halo = QColor(self._color)
        halo.setAlpha(46)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(x, y), 9, 9)
        painter.setBrush(self._color)
        painter.drawEllipse(QPointF(x, y), 5, 5)
        painter.setPen(QPen(QColor(p["text_secondary"])))
        painter.drawText(
            QRectF(right + 6, 0, 44, self.height()), Qt.AlignVCenter | Qt.AlignLeft, f"{round(self._fraction * 100)}%"
        )


class RouteRow(QFrame):
    clicked = Signal(int)

    def __init__(self, shipment: Shipment, selected: bool, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.shipment = shipment
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("routeRow")
        edge = f"border-left: 2px solid {GOLD};" if selected else "border-left: 2px solid transparent;"
        bg = "rgba(225, 173, 102, 18)" if selected else "transparent"
        self.setStyleSheet(
            f"#routeRow {{ background-color: {bg}; border: none; {edge} border-bottom: 1px solid {p['border']}; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(12)

        status = live_status(shipment)
        left = QVBoxLayout()
        left.setSpacing(0)
        origin = QLabel(shipment.origin)
        origin.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        number = QLabel(shipment.number)
        number.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        left.addWidget(origin)
        left.addWidget(number)
        left_box = QWidget()
        left_box.setFixedWidth(190)
        left_box.setLayout(left)
        layout.addWidget(left_box)

        departed = QLabel(local_time_text(shipment.departed_at) if shipment.departed_at else "")
        departed.setFixedWidth(52)
        departed.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(departed)

        self.bar = RouteBar()
        self.bar.set_state(progress(shipment), STATUS_COLORS.get(status, GOLD))
        layout.addWidget(self.bar, stretch=1)

        right = QVBoxLayout()
        right.setSpacing(0)
        dest = QLabel(shipment.dealership_name)
        dest.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        eta_color = WARN if status == "Delayed" else "#d7d3d3"
        eta = QLabel(eta_text(shipment))
        eta.setStyleSheet(f"font-size: 11px; color: {eta_color};")
        for label in (dest, eta):
            label.setAlignment(Qt.AlignRight)
            right.addWidget(label)
        right_box = QWidget()
        right_box.setFixedWidth(230)
        right_box.setLayout(right)
        layout.addWidget(right_box)
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.shipment.id)
        super().mousePressEvent(event)


class RouteTracker(QWidget):
    route_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(16, 8, 16, 6)
        self._header_labels = []
        for text, width, align in (("Origin", 190, Qt.AlignLeft), ("Departed", 52, Qt.AlignLeft),
                                   ("Progress", 0, Qt.AlignCenter), ("Destination · ETA", 230, Qt.AlignRight)):
            label = QLabel(text)
            label.setAlignment(align)
            label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
            if width:
                label.setFixedWidth(width)
                header.addWidget(label)
            else:
                header.addWidget(label, stretch=1)
                self._progress_header = label
        outer.addLayout(header)
        self._rows = QVBoxLayout()
        self._rows.setSpacing(0)
        outer.addLayout(self._rows)
        self._empty = QLabel("No shipments on the road.")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']}; padding: 20px;")
        outer.addWidget(self._empty)
        self._row_widgets: list[RouteRow] = []

    def set_routes(self, shipments: list[Shipment], selected_id: int | None, now_label: str) -> None:
        for row in self._row_widgets:
            self._rows.removeWidget(row)
            row.hide()
            row.deleteLater()
        self._row_widgets = []
        for shipment in shipments:
            row = RouteRow(shipment, shipment.id == selected_id)
            row.clicked.connect(self.route_clicked.emit)
            self._rows.addWidget(row)
            self._row_widgets.append(row)
        self._empty.setVisible(not shipments)
        self._progress_header.setText(f"Progress · {now_label}")

    def rows(self) -> list[RouteRow]:
        return list(self._row_widgets)
