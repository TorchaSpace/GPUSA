"""Overview page - the real build of Inventory Dashboard.dc.html: three
metric cards, the stock-by-location grid and today's employee log.

All real, through the repositories; every number is computed by
shared.overview / shared.analytics.
- "Total revenue · MTD": this month's sales and the change against the
  same stretch of last month, with the regions behind it. (The mockup's
  "Wholesale / Dealer / Direct" split needs a sales channel nothing here
  records - see shared/analytics.py - so it is by dealership region.)
- "Warehouses": share of the combined set capacity in use, and a bar per
  warehouse; a warehouse without a capacity says so instead of faking one.
- "Dealerships": how many are active and how many sold anything this
  month, today's sales, and units on the road. (The mockup's "below
  target" and "avg. fill" need targets/order data that don't exist.)
- "Stock by location": every product against each warehouse, the
  dealerships together and unassigned stock, with the Location filter,
  "Below reorder only" and a name filter. Status is judged on the whole
  network even when a filter hides columns.
- "Employee log": who has checked in today.
- "Pending approvals <n>": the live count of purchase orders held for
  approval (kept current by MainWindow's poller). The mockup opens a
  dropdown of the held requests; here it opens the Purchase Requests page,
  which has the same approval queue - one place to approve from.
Dropped from the mockup: the sales-history chart (that is Reports now) and
the header's search box (a control that does nothing is worse than none).
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.components.segment_button import SegmentButton
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.gui.components.styled_table import cell, styled_table
from admin_app.theme import CLASSICAL_PALETTE
from database import (
    attendance_repository,
    dealership_repository,
    product_repository,
    stock_repository,
    transaction_repository,
    warehouse_repository,
)
from database.exceptions import DATABASE_ERRORS
from shared import analytics, overview
from shared.formatting import format_amount, local_time_text

_FILTER_LABELS = (
    (overview.FILTER_ALL, "All"),
    (overview.FILTER_WAREHOUSES, "Warehouses"),
    (overview.FILTER_DEALERSHIPS, "Dealerships"),
)
_STATUS_COLORS = {
    overview.STATUS_OUT: CLASSICAL_PALETTE["alert_critical"],
    overview.STATUS_LOW: CLASSICAL_PALETTE["alert_warning"],
    overview.STATUS_IN: CLASSICAL_PALETTE["alert_success"],
}


def _bar() -> QProgressBar:
    p = CLASSICAL_PALETTE
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setTextVisible(False)
    bar.setFixedHeight(5)
    bar.setStyleSheet(
        f"QProgressBar {{ background: {p['border']}; border: none; border-radius: 2px; }}"
        f"QProgressBar::chunk {{ background: {p['text_secondary']}; border-radius: 2px; }}"
    )
    return bar


class OverviewPage(AdminPage):
    open_purchase_requests = Signal()

    def __init__(self, parent: QWidget | None = None, today_provider=date.today):
        super().__init__("Operations Overview", parent)
        self._today_provider = today_provider  # injectable so tests can pin "today"
        self._filter = overview.FILTER_ALL
        self._low_only = False
        self._query = ""
        self._products = []
        self._levels = []
        self._warehouses = []
        self._roster = []

        self._approvals_button = CompactButton("Pending approvals")
        self._approvals_button.setToolTip("Purchase orders held for your approval")
        self._approvals_button.clicked.connect(self.open_purchase_requests.emit)
        self.add_header_action(self._approvals_button)

        refresh_button = CompactButton("Refresh")
        refresh_button.clicked.connect(self.reload)
        self.add_header_action(refresh_button)

        self.body_layout().addWidget(self._build_metric_row())
        self.body_layout().addWidget(self._build_stock_section(), stretch=1)
        self.body_layout().addWidget(self._build_attendance_section())

        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()  # sales, stock and check-ins all change while this page is closed

    # --- building -----------------------------------------------------

    def _build_metric_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self._revenue_card = StatCard("Total revenue · MTD", "—")
        self._warehouse_card = StatCard("Warehouses", "—")
        self._dealership_card = StatCard("Dealerships", "—")
        for card in (self._revenue_card, self._warehouse_card, self._dealership_card):
            layout.addWidget(card, stretch=1)
        return row

    def _build_stock_section(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._stock_section = Section("Stock by location", "Product inventory")

        self._filter_group = QButtonGroup(self)
        self._filter_buttons: dict[str, SegmentButton] = {}
        for key, label in _FILTER_LABELS:
            button = SegmentButton(label)
            button.clicked.connect(lambda _c=False, k=key: self.set_location_filter(k))
            self._filter_group.addButton(button)
            self._filter_buttons[key] = button
            self._stock_section.add_header_control(button)
        self._filter_buttons[overview.FILTER_ALL].setChecked(True)

        self._low_only_checkbox = QCheckBox("Below reorder only")
        self._low_only_checkbox.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        self._low_only_checkbox.toggled.connect(self.set_low_only)
        self._stock_section.add_header_control(self._low_only_checkbox)

        self._query_input = QLineEdit()
        self._query_input.setPlaceholderText("Filter SKU or name")
        self._query_input.setMaximumWidth(180)
        self._query_input.textChanged.connect(self.set_query)
        self._stock_section.add_header_control(self._query_input)

        self._stock_table = styled_table(["SKU", "Product"])
        self._stock_table.setMinimumHeight(300)
        self._stock_section.body_layout().addWidget(self._stock_table)

        self._stock_footer = QLabel("")
        self._stock_footer.setStyleSheet(f"color: {p['text_secondary']}; font-size: 11px; padding: 8px 16px;")
        self._stock_section.body_layout().addWidget(self._stock_footer)
        return self._stock_section

    def _build_attendance_section(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._attendance_section = Section("Attendance · today", "Employee log")
        self._attendance_summary = QLabel("")
        self._attendance_summary.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        self._attendance_section.add_header_control(self._attendance_summary)
        self._attendance_table = styled_table(["Employee", "Site", "In", "Out", "Status"])
        self._attendance_table.setMinimumHeight(180)
        self._attendance_section.body_layout().addWidget(self._attendance_table)
        return self._attendance_section

    # --- state --------------------------------------------------------

    def set_pending_count(self, count: int) -> None:
        self._approvals_button.setText(f"Pending approvals  {count}" if count else "Pending approvals")

    def set_location_filter(self, key: str) -> None:
        self._filter = key
        self._filter_buttons[key].setChecked(True)
        self._render_stock()

    def set_low_only(self, checked: bool) -> None:
        self._low_only = checked
        self._render_stock()

    def set_query(self, text: str) -> None:
        self._query = text
        self._render_stock()

    # --- data ---------------------------------------------------------

    def reload(self) -> None:
        today = self._today_provider()
        period = analytics.period_for("month", today)
        try:
            self._products = product_repository.list_all()
            self._levels = stock_repository.all_levels()
            self._warehouses = warehouse_repository.list_all()
            used = stock_repository.units_by_location()
            on_the_road = stock_repository.units_in_transit()
            dealerships = dealership_repository.list_all()
            sales = transaction_repository.list_between(period.start_datetime, period.end_exclusive)
            previous = transaction_repository.list_between(period.prev_start_datetime, period.prev_end_exclusive)
            self._roster = attendance_repository.list_roster()
        except DATABASE_ERRORS:
            self._products, self._levels, self._warehouses, self._roster = [], [], [], []
            self._stock_footer.setText("Couldn't load the figures from the database.")
            return
        self._render_revenue(period, sales, previous, dealerships)
        self._render_warehouses(overview.capacity_summary(self._warehouses, used))
        self._render_dealerships(
            overview.dealership_summary(dealerships, {t.dealership_code for t in sales if t.dealership_code}),
            analytics.sales_today(sales, today), on_the_road,
        )
        self._render_stock()
        self._render_attendance()

    # --- rendering ----------------------------------------------------

    def _render_revenue(self, period, sales, previous, dealerships) -> None:
        revenue = round(sum(t.total for t in sales), 2)
        change = analytics.percent_change(revenue, round(sum(t.total for t in previous), 2))
        self._revenue_card.set_value(format_amount(revenue))
        self._revenue_card.set_trend(
            analytics.change_text(change) if change is not None else "", None if change is None else change >= 0
        )
        self._revenue_card.set_corner_note(f"vs {period.prev_start:%b}" if change is not None else "")
        self._revenue_card.clear_footer()
        regions = [(r, v) for r, v in analytics.revenue_by_region(sales, dealerships).items() if v > 0]
        regions.sort(key=lambda pair: -pair[1])
        footer = self._revenue_card.footer_layout()
        if not regions:
            footer.addWidget(stat_breakdown_item("This month", "No sales yet"))
        for region, value in regions[:3]:
            footer.addWidget(stat_breakdown_item(region, analytics.compact_amount(value)))

    def _render_warehouses(self, summary: overview.CapacitySummary) -> None:
        p = CLASSICAL_PALETTE
        self._warehouse_card.set_value("—" if summary.overall is None else f"{round(summary.overall * 100)}%")
        self._warehouse_card.set_trend("capacity used" if summary.overall is not None else "no capacities set", None)
        self._warehouse_card.set_corner_note(f"{summary.active} active")
        self._warehouse_card.clear_footer()
        footer = self._warehouse_card.footer_layout()
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        if not summary.warehouses:
            note = QLabel("No warehouses yet")
            note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; border: none;")
            rows.addWidget(note)
        for w in summary.warehouses:
            line = QWidget()
            h = QHBoxLayout(line)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            name = QLabel(w.code)
            name.setFixedWidth(64)
            name.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; border: none;")
            bar = _bar()
            bar.setValue(0 if w.fraction is None else min(100, round(w.fraction * 100)))
            if w.fraction is not None and w.fraction >= 0.85:
                bar.setStyleSheet(bar.styleSheet().replace(p["text_secondary"], p["accent"]))
            pct = QLabel("—" if w.fraction is None else f"{round(w.fraction * 100)}%")
            pct.setFixedWidth(40)
            pct.setAlignment(Qt.AlignRight)
            pct.setStyleSheet(f"font-size: 12px; color: {p['text_primary']}; border: none;")
            h.addWidget(name)
            h.addWidget(bar, stretch=1)
            h.addWidget(pct)
            rows.addWidget(line)
        footer.addWidget(holder, stretch=1)

    def _render_dealerships(self, summary: overview.DealershipSummary, today: tuple[int, float], on_road: int) -> None:
        self._dealership_card.set_value(str(summary.active))
        self._dealership_card.set_trend(
            f"active · {summary.silent} with no sales this month" if summary.silent else "active · all selling", None
        )
        regions = len(summary.by_region)
        self._dealership_card.set_corner_note(f"{regions} region{'s' if regions != 1 else ''}")
        self._dealership_card.clear_footer()
        footer = self._dealership_card.footer_layout()
        footer.addWidget(stat_breakdown_item("Sales today", str(today[0])))
        footer.addWidget(stat_breakdown_item("Revenue today", analytics.compact_amount(today[1])))
        footer.addWidget(stat_breakdown_item("Units on the road", f"{on_road:,}"))

    def _render_stock(self) -> None:
        grid = overview.stock_grid(
            self._products, self._levels, self._warehouses, self._filter, self._low_only, self._query
        )
        headers = ["SKU", "Product", *grid.columns, "Total", "Reorder at", "Status"]
        table = self._stock_table
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(grid.rows))
        first_number = 2
        for r, row in enumerate(grid.rows):
            table.setItem(r, 0, cell(row.barcode))
            table.setItem(r, 1, cell(row.name))
            for c, value in enumerate(row.cells):
                zero = value == 0
                table.setItem(
                    r, first_number + c,
                    cell(f"{value:,}", right=True, color=CLASSICAL_PALETTE["accent"] if zero else None),
                )
            total_col = first_number + len(row.cells)
            table.setItem(r, total_col, cell(f"{row.total:,}", right=True))
            table.setItem(r, total_col + 1, cell(f"{row.reorder_at:,}", right=True))
            status = cell(row.status)
            status.setForeground(QColor(_STATUS_COLORS[row.status]))
            table.setItem(r, total_col + 2, status)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        shown_note = "" if self._filter == overview.FILTER_ALL else " · totals cover the visible columns, status the whole network"
        self._stock_footer.setText(f"{len(grid.rows)} of {len(self._products)} products · units{shown_note}")

    def _render_attendance(self) -> None:
        today = overview.attendance_today(self._roster)
        self._attendance_summary.setText(
            f"{today.on_site} on site · {today.checked_out} checked out · {today.not_in} not in yet"
        )
        table = self._attendance_table
        table.setRowCount(len(today.rows))
        for r, entry in enumerate(today.rows):
            table.setItem(r, 0, cell(entry["name"]))
            table.setItem(r, 1, cell(entry["location_name"]))
            table.setItem(r, 2, cell(local_time_text(entry["check_in_at"]), right=True))
            table.setItem(r, 3, cell(local_time_text(entry["check_out_at"]), right=True))
            table.setItem(r, 4, cell(entry["status"]))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
