"""One warehouse card for the Console's Warehouses page (the Warehouse
Console mockup's WH-01/WH-02/WH-03 row), Industry-styled: code · city
kicker, name, a capacity bar (units held vs. capacity), status, and the
SKU / dock / today's in-out line. Clicking a card selects it
(`clicked(code)`); the selected one gets the accent border, and this
depot's own warehouse is marked "THIS DEPOT".

Numbers are real (see shared/warehousing.py): used = units in its stock
levels, capacity as set in Admin (or "capacity not set"). The mockup's
pallet counts and dock occupancy ("6/8") aren't tracked, so they're not
shown - instead the card shows the free units (capacity minus held) and the
number of docks.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.models import Warehouse
from shared.i18n import enum_label, plural, tr
from shared.textcase import upper
from shared.warehousing import STATUS_NEAR, capacity_fraction, capacity_status, percent_text

_AMBER = "#f4b400"


class CapacityBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fraction: float | None = None
        self._color = QColor(INDUSTRY_PALETTE["accent"])
        self.setFixedHeight(8)

    def set_fraction(self, fraction: float | None, color: str) -> None:
        self._fraction = fraction
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(INDUSTRY_PALETTE["border"]))
        if self._fraction:
            width = self.width() * min(1.0, self._fraction)
            painter.fillRect(QRectF(0, 0, width, self.height()), self._color)


class WarehouseCard(BlueprintFrame):
    clicked = Signal(str)

    def __init__(self, warehouse: Warehouse, is_this_depot: bool, parent=None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        self.warehouse = warehouse
        self._is_this_depot = is_this_depot
        self.setObjectName("warehouseCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        kicker = QLabel(" · ".join(part for part in (warehouse.code, warehouse.city) if part))
        kicker.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        top.addWidget(kicker, stretch=1)
        if is_this_depot:
            mine = QLabel(tr("depot.card.this_depot"))
            mine.setStyleSheet(f"font-size: 10px; letter-spacing: 1px; color: {p['accent_900']}; "
                               f"background-color: {p['accent_100']}; padding: 1px 6px;")
            top.addWidget(mine)
        layout.addLayout(top)

        name = QLabel(warehouse.name)
        name.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(name)

        usage_row = QHBoxLayout()
        self.usage_label = QLabel()
        self.usage_label.setStyleSheet(f"font-size: 12px; color: {p['text_primary']};")
        usage_row.addWidget(self.usage_label, stretch=1)
        self.status_label = QLabel()
        usage_row.addWidget(self.status_label)
        layout.addLayout(usage_row)

        self.bar = CapacityBar()
        layout.addWidget(self.bar)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self.detail_label)
        self.set_selected(False)

        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_selected(self, selected: bool) -> None:
        p = INDUSTRY_PALETTE
        edge = p["accent"] if selected else p["border"]
        width = 2 if selected else 1
        self.setStyleSheet(f"#warehouseCard {{ background-color: {p['surface']}; border: {width}px solid {edge}; }}")

    def set_numbers(self, used_units: int, sku_count: int, inbound_today: int, outbound_today: int) -> None:
        p = INDUSTRY_PALETTE
        w = self.warehouse
        fraction = capacity_fraction(used_units, w.capacity_units)
        status = capacity_status(w, used_units)
        if w.capacity_units:
            self.usage_label.setText(tr("depot.card.usage").format(used=f"{used_units:,}", capacity=f"{w.capacity_units:,}", percent=percent_text(fraction)))
        else:
            self.usage_label.setText(tr("depot.card.usage_unset").format(used=f"{used_units:,}"))
        near = status == STATUS_NEAR
        self.status_label.setText(upper(enum_label("wh_status", status)))
        self.status_label.setStyleSheet(
            f"font-size: 10px; letter-spacing: 1px; padding: 1px 6px; "
            f"color: {p['text_primary'] if near else p['accent_900']}; "
            f"background-color: {'#fff6d6' if near else p['accent_100']}; "
            f"border: 1px solid {_AMBER if near else p['accent']};"
        )
        self.bar.set_fraction(fraction, _AMBER if near else p["accent"])
        docks = plural("depot.card.docks", w.docks) if w.docks else tr("depot.card.docks_unset")
        free = (tr("depot.card.free_units").format(free=f"{max(0, w.capacity_units - used_units):,}")
                if w.capacity_units else tr("depot.card.free_unknown"))
        self.detail_label.setText(
            f"{plural('depot.card.skus_held', sku_count)} · {free}\n"
            + tr("depot.card.detail_line").format(docks=docks, inbound=inbound_today, outbound=outbound_today)
        )

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self.warehouse.code)
        super().mousePressEvent(event)
