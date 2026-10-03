"""One card of the Warehouses mockup's network row, Classical-styled:
name + status pill, "City · code · N docks", the capacity bar with its
percentage and "used / capacity units", then on shift / rostered and
today's inbound / outbound units. Clicking the card toggles it as the
page's site filter (`clicked(code)`); the selected card gets the gold
edge, like the route tracker's selected row.

All numbers are real (shared/warehousing.py): the mockup's pallets are
units of stock here, "on shift" is employees of that warehouse checked
in right now, "rostered" its active employees.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from shared.i18n import enum_label, plural, tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from shared.models import NEAR_CAPACITY_THRESHOLD, Warehouse
from shared.formatting import format_int
from shared.warehousing import STATUS_NEAR, STATUS_OK, capacity_fraction, capacity_status, percent_text


class _Bar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction: float | None = None
        self._color = QColor(CLASSICAL_PALETTE["accent"])
        self.setFixedHeight(6)

    def set_fraction(self, fraction: float | None, color: str) -> None:
        self._fraction, self._color = fraction, QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["border"]))
        painter.drawRoundedRect(QRectF(self.rect()), 3, 3)
        if self._fraction:
            painter.setBrush(self._color)
            painter.drawRoundedRect(QRectF(0, 0, self.width() * min(1.0, self._fraction), self.height()), 3, 3)
        if self._fraction is None:
            return  # no capacity set: no threshold to mark
        # The 85% threshold tick.
        painter.setBrush(QColor(p["text_secondary"]))
        x = self.width() * NEAR_CAPACITY_THRESHOLD
        painter.drawRect(QRectF(x - 0.5, 0, 1, self.height()))


def _stat(label: str) -> tuple[QWidget, QLabel]:
    p = CLASSICAL_PALETTE
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    caption = QLabel(label)
    caption.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
    value = QLabel("—")
    value.setStyleSheet(f"font-size: 14px; color: {p['text_primary']};")
    layout.addWidget(caption)
    layout.addWidget(value)
    return box, value


class WarehouseCapacityCard(QFrame):
    clicked = Signal(str)

    def __init__(self, warehouse: Warehouse, parent=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.warehouse = warehouse
        self.setObjectName("whCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        name = QLabel(warehouse.name)
        name.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 20px; color: {p['text_primary']};")
        top.addWidget(name, stretch=1)
        self.status_label = QLabel()
        top.addWidget(self.status_label, alignment=Qt.AlignTop)
        layout.addLayout(top)

        docks = (
            plural("admin.warehouses.card_docks", warehouse.docks) if warehouse.docks
            else tr("admin.warehouses.card_docks_unset")
        )
        meta = QLabel(" · ".join(part for part in (warehouse.city, warehouse.code, docks) if part))
        meta.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(meta)

        cap_row = QHBoxLayout()
        cap_caption = QLabel(tr("admin.warehouses.card_capacity"))
        cap_caption.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        cap_row.addWidget(cap_caption, stretch=1)
        self.percent_label = QLabel()
        self.percent_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 18px; color: {p['text_primary']};")
        cap_row.addWidget(self.percent_label)
        layout.addLayout(cap_row)
        self.bar = _Bar()
        layout.addWidget(self.bar)
        self.units_label = QLabel()
        self.units_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self.units_label)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        shift_box, self.shift_label = _stat(tr("admin.warehouses.card_on_shift"))
        in_box, self.inbound_label = _stat(tr("admin.warehouses.card_inbound"))
        out_box, self.outbound_label = _stat(tr("admin.warehouses.card_outbound"))
        for box in (shift_box, in_box, out_box):
            stats.addWidget(box)
        stats.addStretch(1)
        layout.addLayout(stats)

        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        p = CLASSICAL_PALETTE
        edge = f"border-left: 2px solid {p['accent']};" if selected else ""
        bg = "rgba(225, 173, 102, 18)" if selected else p["surface"]
        self.setStyleSheet(
            f"#whCard {{ background-color: {bg}; border: 1px solid {p['border']}; "
            f"border-radius: {p['radius_md']}; {edge} }}"
        )

    def set_numbers(self, used_units: int, on_shift: int, rostered: int, inbound: int, outbound: int) -> None:
        p = CLASSICAL_PALETTE
        w = self.warehouse
        fraction = capacity_fraction(used_units, w.capacity_units)
        status = capacity_status(w, used_units)
        color = {STATUS_NEAR: p["alert_warning"], STATUS_OK: p["alert_success"]}.get(status, p["text_secondary"])
        self.status_label.setText(enum_label("wh_status", status))
        self.status_label.setStyleSheet(
            f"font-size: 11px; color: {color}; border: 1px solid {color}; border-radius: 9px; padding: 1px 8px;"
        )
        self.percent_label.setText(percent_text(fraction))
        self.bar.set_fraction(fraction, p["alert_warning"] if status == STATUS_NEAR else p["accent"])
        over = bool(w.capacity_units) and used_units > w.capacity_units
        self.percent_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 18px; color: {p['alert_critical'] if over else p['text_primary']};"
        )
        if over:
            self.units_label.setText(
                tr("admin.warehouses.card_over").format(
                    used=format_int(used_units), cap=format_int(w.capacity_units),
                    over_text=tr("admin.wh_over_capacity"), over=format_int(used_units - w.capacity_units),
                )
            )
        elif w.capacity_units:
            self.units_label.setText(
                tr("admin.warehouses.card_units_cap").format(used=format_int(used_units), cap=format_int(w.capacity_units))
            )
        else:
            self.units_label.setText(tr("admin.warehouses.card_units_nocap").format(used=format_int(used_units)))
        self.shift_label.setText(f"{on_shift} / {rostered}")
        self.inbound_label.setText(f"+{format_int(inbound)}")
        self.outbound_label.setText(f"−{format_int(outbound)}")

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.warehouse.code)
        super().mousePressEvent(event)
