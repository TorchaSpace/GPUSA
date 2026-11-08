"""Dealership cards + the expanded detail - the Dealerships mockup's
card grid (Dealerships.dc.html): name, city · code, region pill, revenue
this month, a 7-day sparkline with its trend and the number of active
employees; clicking a card opens the detail across the full width
under its row, with the staff list and stock on hand.

Everything shown is real: revenue from the sales each till recorded with
its dealership code (net of refunds), staff from Workforce (employees
placed at the dealership by name or code), stock from the dealership's
own shelf.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from admin_app.gui.components.charts import Sparkline
from admin_app.gui.components.compact_button import CompactButton
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from shared.analytics import change_text, daily_totals, percent_change, revenue_between
from shared.currency import format_money
from shared.formatting import format_int
from shared.i18n import plural, region_label, tr
from shared.models import Dealership, Employee, Product, Transaction


@dataclass
class DealershipStats:
    revenue_month: float = 0.0
    week: list[float] = field(default_factory=lambda: [0.0] * 7)
    trend: float | None = None  # percent, last 3 days vs the 4 before
    staff: list[Employee] = field(default_factory=list)


def _week_trend(week: list[float]) -> float | None:
    earlier, recent = sum(week[:4]), sum(week[4:])
    return percent_change(recent / 3, earlier / 4) if earlier > 0 else None


def _same(a: str, b: str) -> bool:
    return " ".join((a or "").split()).casefold() == " ".join((b or "").split()).casefold()


def build_stats(dealerships: list[Dealership], transactions: list[Transaction], employees: list[Employee],
                today: date) -> dict[str, DealershipStats]:
    """Per dealership code: revenue since the 1st of `today`'s month, the
    last 7 days of revenue (ending today) and its active staff.
    `transactions` must cover both windows."""
    by_code: dict[str, list[Transaction]] = {}
    for transaction in transactions:
        if transaction.dealership_code:
            by_code.setdefault(transaction.dealership_code.upper(), []).append(transaction)
    month_start = today.replace(day=1)
    stats: dict[str, DealershipStats] = {}
    for dealership in dealerships:
        sales = by_code.get(dealership.code.upper(), [])
        week = daily_totals(sales, today - timedelta(days=6), today)
        staff = [
            e for e in employees
            if e.is_active and e.location_type == "Dealership"
            and (_same(e.location_name, dealership.name) or _same(e.location_name, dealership.code))
        ]
        stats[dealership.code] = DealershipStats(
            revenue_between(sales, month_start, today), week, _week_trend(week), sorted(staff, key=lambda e: e.name)
        )
    return stats


def _pill(text: str, color: str | None = None) -> QLabel:
    p = CLASSICAL_PALETTE
    pill = QLabel(text.upper())
    pill.setStyleSheet(
        f"font-size: 10px; letter-spacing: 1px; padding: 2px 8px; color: {color or p['text_secondary']}; "
        f"border: 1px solid {color or p['border']}; border-radius: 9px;"
    )
    pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return pill


def _kicker(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(f"font-size: 10px; letter-spacing: 1px; color: {CLASSICAL_PALETTE['text_secondary']};")
    return label


def _trend_color(trend: float | None) -> str:
    return CLASSICAL_PALETTE["accent"] if trend is None or trend >= 0 else "#7d7979"


def _trend_text(trend: float | None) -> str:
    return tr("admin.dealerships.trend_7d").format(change=change_text(trend) if trend is not None else "—")


class DealershipCard(QFrame):
    """One dealership; clicked(code) on a click anywhere on it."""

    clicked = Signal(str)

    def __init__(self, dealership: Dealership, stats: DealershipStats, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.dealership = dealership
        self.setObjectName("dealershipCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(8)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.name_label = QLabel(dealership.name)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 21px; color: {p['text_primary']};")
        titles.addWidget(self.name_label)
        where = QLabel(f"{dealership.city} · {dealership.code}")
        where.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        titles.addWidget(where)
        top.addLayout(titles, stretch=1)
        pills = QVBoxLayout()
        pills.setSpacing(4)
        pills.addWidget(_pill(region_label(dealership.region)), alignment=Qt.AlignRight)
        if not dealership.is_active:
            pills.addWidget(_pill(tr("admin.dealerships.status_inactive"), p["alert_critical"]), alignment=Qt.AlignRight)
        pills.addStretch(1)
        top.addLayout(pills)
        layout.addLayout(top)

        money = QHBoxLayout()
        revenue = QVBoxLayout()
        revenue.setSpacing(2)
        revenue.addWidget(_kicker(tr("admin.dealerships.revenue_mtd")))
        self.revenue_label = QLabel(format_money(stats.revenue_month))
        self.revenue_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 30px; color: {p['text_primary']};")
        revenue.addWidget(self.revenue_label)
        money.addLayout(revenue, stretch=1)
        trend = QVBoxLayout()
        trend.setSpacing(2)
        spark = Sparkline()
        spark.setFixedSize(110, 32)
        spark.set_values(stats.week, up=stats.trend is None or stats.trend >= 0)
        trend.addWidget(spark, alignment=Qt.AlignRight)
        trend_label = QLabel(_trend_text(stats.trend))
        trend_label.setStyleSheet(f"font-size: 11px; color: {_trend_color(stats.trend)};")
        trend.addWidget(trend_label, alignment=Qt.AlignRight)
        money.addLayout(trend)
        layout.addLayout(money)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color: {p['border']}; border: none;")
        layout.addWidget(rule)

        bottom = QHBoxLayout()
        staff = QLabel(plural("admin.dealerships.staff_count", len(stats.staff)))
        staff.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        bottom.addWidget(staff)
        bottom.addStretch(1)
        self.details_button = CompactButton(tr("admin.dealerships.view_details"), variant="ghost")
        self.details_button.clicked.connect(lambda: self.clicked.emit(self.dealership.code))
        bottom.addWidget(self.details_button)
        layout.addLayout(bottom)

        self.set_selected(False)
        if not dealership.is_active:
            for label in (self.name_label, self.revenue_label):
                label.setStyleSheet(label.styleSheet().replace(p["text_primary"], p["text_secondary"]))

    def set_selected(self, selected: bool) -> None:
        p = CLASSICAL_PALETTE
        border = p["accent"] if selected else p["border"]
        self.setStyleSheet(
            f"#dealershipCard {{ background-color: {p['background']}; border: 1px solid {border}; "
            f"border-radius: {p['radius_md']}; }}"
            f"#dealershipCard:hover {{ border-color: {p['accent']}; }}"
            "QLabel { border: none; background: transparent; }"
        )

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.dealership.code)
        super().mouseReleaseEvent(event)


class _StockRow(QWidget):
    def __init__(self, product: Product, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(12)
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel(product.name)
        name.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        names.addWidget(name)
        sku = QLabel(product.barcode)
        sku.setStyleSheet(f"font-size: 10.5px; color: {p['text_secondary']};")
        names.addWidget(sku)
        layout.addLayout(names, stretch=1)

        level = product.critical_stock_level
        low = product.stock_quantity <= 0 or (level > 0 and product.stock_quantity <= level)
        color = p["alert_critical"] if low else p["text_primary"]
        par = max(level * 2, product.stock_quantity, 1)
        bar = QFrame()
        bar.setFixedSize(90, 6)
        bar.setStyleSheet(f"border: 1px solid {p['border']}; background: transparent;")
        fill = QFrame(bar)
        fill.setGeometry(0, 0, max(int(90 * min(product.stock_quantity / par, 1.0)), 0), 6)
        fill.setStyleSheet(f"background: {p['alert_critical'] if low else '#d7d3d3'}; border: none;")
        layout.addWidget(bar)
        units = QLabel(format_int(product.stock_quantity) + (f" / {format_int(level)}" if level > 0 else ""))
        units.setFixedWidth(80)
        units.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        units.setStyleSheet(f"font-size: 12px; color: {color};")
        layout.addWidget(units)


class DealershipDetail(QFrame):
    """The expanded card: header with the numbers, the actions, then two
    columns - staff and stock on hand."""

    close_requested = Signal()

    _STOCK_ROWS = 8

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.setObjectName("dealershipDetail")
        self.setStyleSheet(
            f"#dealershipDetail {{ background-color: {p['surface']}; border: 1px solid {p['accent']}; "
            f"border-radius: {p['radius_md']}; }}"
            "QLabel { border: none; background: transparent; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QWidget()
        head.setObjectName("detailHead")
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setStyleSheet(f"#detailHead {{ border-bottom: 1px solid {p['border']}; }}")
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(18, 16, 18, 14)
        head_layout.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.name_label = QLabel()
        self.name_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 28px; color: {p['text_primary']};")
        title_row.addWidget(self.name_label)
        self._pills = QHBoxLayout()
        self._pills.setSpacing(6)
        title_row.addLayout(self._pills)
        title_row.addStretch(1)
        self.close_button = CompactButton(tr("admin.dealerships.close_details"))
        self.close_button.clicked.connect(self.close_requested.emit)
        title_row.addWidget(self.close_button)
        head_layout.addLayout(title_row)

        self.code_label = QLabel()
        self.code_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        head_layout.addWidget(self.code_label)

        self.metrics = QHBoxLayout()
        self.metrics.setSpacing(36)
        head_layout.addLayout(self.metrics)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.edit_button = CompactButton(tr("admin.dealerships.edit"))
        self.levels_button = CompactButton(tr("admin.dealerships.reorder_levels"))
        self.opening_button = CompactButton(tr("admin.dealerships.opening_stock"))
        self.delete_button = CompactButton(tr("admin.dealerships.delete"))
        for button in (self.edit_button, self.levels_button, self.opening_button):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(self.delete_button)
        head_layout.addLayout(actions)
        outer.addWidget(head)

        columns = QWidget()
        grid = QHBoxLayout(columns)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        self._staff_box, self._staff_list = self._column(tr("admin.dealerships.staff"), tr("admin.dealerships.staff_hint"))
        self._stock_box, self._stock_list = self._column(tr("admin.dealerships.stock_on_hand"),
                                                         tr("admin.dealerships.units_par"))
        self._staff_box.setObjectName("staffBox")
        self._staff_box.setAttribute(Qt.WA_StyledBackground, True)
        self._staff_box.setStyleSheet(f"#staffBox {{ border-right: 1px solid {p['border']}; }}")
        grid.addWidget(self._staff_box, stretch=1)
        grid.addWidget(self._stock_box, stretch=1)
        outer.addWidget(columns)

    def _column(self, title: str, hint: str) -> tuple[QWidget, QVBoxLayout]:
        p = CLASSICAL_PALETTE
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(0)
        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 20px; color: {p['text_primary']};")
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(_kicker(hint), alignment=Qt.AlignBottom)
        layout.addLayout(header)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color: {p['border']}; border: none; margin-top: 6px;")
        layout.addWidget(rule)
        rows = QVBoxLayout()
        rows.setSpacing(0)
        layout.addLayout(rows)
        layout.addStretch(1)
        return box, rows

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _metric(self, label: str, value: str, color: str | None = None, extra: QWidget | None = None) -> QWidget:
        p = CLASSICAL_PALETTE
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(_kicker(label))
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 30px; color: {color or p['text_primary']};")
        layout.addWidget(value_label)
        if extra is not None:
            layout.addWidget(extra)
        return box

    def show_dealership(self, dealership: Dealership, stats: DealershipStats, stock: list[Product],
                        status: tuple[str, str]) -> None:
        p = CLASSICAL_PALETTE
        self.name_label.setText(dealership.name)
        self.code_label.setText(dealership.code)
        self._clear(self._pills)
        self._pills.addWidget(_pill(region_label(dealership.region)))
        manager = dealership.manager_name or "—"
        self.code_label.setText(tr("admin.dealerships.detail_sub").format(
            city=dealership.city, code=dealership.code, manager=manager))
        self.code_label.setProperty("code", dealership.code)

        self._clear(self.metrics)
        self.metrics.addWidget(self._metric(tr("admin.dealerships.col_status"), status[0], status[1]))
        self.metrics.addWidget(self._metric(tr("admin.dealerships.revenue_mtd"), format_money(stats.revenue_month)))
        self.metrics.addWidget(self._metric(tr("admin.dealerships.active_staff"), str(len(stats.staff))))
        units = sum(max(product.stock_quantity, 0) for product in stock)
        self.metrics.addWidget(self._metric(
            tr("admin.dealerships.stock_on_hand"), format_int(units),
            extra=_kicker(plural("common.sku", sum(1 for product in stock if product.stock_quantity > 0)))))
        spark = Sparkline()
        spark.setFixedSize(200, 40)
        spark.set_values(stats.week, up=stats.trend is None or stats.trend >= 0)
        trend_box = QWidget()
        trend_layout = QVBoxLayout(trend_box)
        trend_layout.setContentsMargins(0, 0, 0, 0)
        trend_layout.setSpacing(4)
        trend_label = QLabel(tr("admin.dealerships.week_sales").format(trend=_trend_text(stats.trend)))
        trend_label.setStyleSheet(f"font-size: 10px; letter-spacing: 1px; color: {_trend_color(stats.trend)};")
        trend_layout.addWidget(trend_label)
        trend_layout.addWidget(spark)
        self.metrics.addWidget(trend_box)
        self.metrics.addStretch(1)

        self._clear(self._staff_list)
        if stats.staff:
            for employee in stats.staff:
                self._staff_list.addWidget(self._line(employee.name, employee.title or employee.role, employee.badge_id))
        else:
            self._staff_list.addWidget(self._empty(tr("admin.dealerships.no_staff")))

        self._clear(self._stock_list)
        stocked = [product for product in stock if product.stocked_here or product.stock_quantity > 0]

        def urgency(product: Product) -> tuple:
            level = product.critical_stock_level
            ratio = product.stock_quantity / level if level > 0 else 99
            return (ratio, product.name.casefold())

        stocked.sort(key=urgency)
        for product in stocked[: self._STOCK_ROWS]:
            self._stock_list.addWidget(_StockRow(product))
        if len(stocked) > self._STOCK_ROWS:
            self._stock_list.addWidget(self._empty(tr("admin.dealerships.more_products").format(
                n=len(stocked) - self._STOCK_ROWS)))
        if not stocked:
            self._stock_list.addWidget(self._empty(tr("admin.dealerships.no_stock")))

    def _line(self, title: str, subtitle: str, right: str) -> QWidget:
        p = CLASSICAL_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 7, 0, 7)
        names = QVBoxLayout()
        names.setSpacing(0)
        top = QLabel(title)
        top.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        names.addWidget(top)
        sub = QLabel(subtitle)
        sub.setStyleSheet(f"font-size: 10.5px; color: {p['text_secondary']};")
        names.addWidget(sub)
        layout.addLayout(names, stretch=1)
        badge = QLabel(right)
        badge.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(badge)
        return row

    def _empty(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"font-size: 12px; color: {CLASSICAL_PALETTE['text_secondary']}; padding: 10px 0;")
        return label


class CardGrid(QWidget):
    """Lays the cards out in as many columns as fit (min 280px each) and
    puts the detail panel, when shown, under the row of its card."""

    MIN_CARD_WIDTH = 280

    def __init__(self, detail: DealershipDetail, parent: QWidget | None = None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(16)
        self._cards: list[DealershipCard] = []
        self._detail = detail
        self._detail_after: str | None = None
        self._columns = 0

    def set_cards(self, cards: list[DealershipCard]) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards = cards
        self._columns = 0
        self._relayout()

    def set_order(self, cards: list[DealershipCard]) -> None:
        """The same cards in a new order (filtering/sorting); laid out on
        the next show_detail_after()."""
        self._cards = cards

    def cards(self) -> list[DealershipCard]:
        return list(self._cards)

    def show_detail_after(self, code: str | None) -> None:
        self._detail_after = code
        self._columns = 0
        self._relayout()

    def _column_count(self) -> int:
        width = max(self.width(), self.MIN_CARD_WIDTH)
        return max(1, (width + 16) // (self.MIN_CARD_WIDTH + 16))

    def _relayout(self) -> None:
        columns = self._column_count()
        self._columns = columns
        while self._grid.count():
            self._grid.takeAt(0)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        visible = [card for card in self._cards if card.property("shown") is not False]
        row = column = 0
        detail_placed = False
        selected_index = next((i for i, c in enumerate(visible) if c.dealership.code == self._detail_after), None)
        detail_row_after = None if selected_index is None else selected_index // columns
        for index, card in enumerate(visible):
            self._grid.addWidget(card, row, column, Qt.AlignTop)
            card.show()
            column += 1
            if column == columns or index == len(visible) - 1:
                if detail_row_after is not None and index // columns == detail_row_after and not detail_placed:
                    row += 1
                    self._grid.addWidget(self._detail, row, 0, 1, columns)
                    self._detail.show()
                    detail_placed = True
                row += 1
                column = 0
        for card in self._cards:
            if card.property("shown") is False:
                card.hide()
        if not detail_placed:
            self._detail.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._column_count() != self._columns:
            self._relayout()
