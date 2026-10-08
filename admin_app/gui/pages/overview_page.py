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

from admin_app.gui.components.animated_checkbox import AnimatedCheckBox
from admin_app.gui.components.notification_center import activity_table, fill_activity_table
from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.components.segment_button import SegmentButton
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.gui.components.styled_table import cell, styled_table
from shared.i18n import enum_label, plural, region_label, tr
from admin_app.theme import CLASSICAL_PALETTE
from database import (
    activity_repository,
    attendance_repository,
    dealership_repository,
    product_repository,
    sale_cost_repository,
    stock_repository,
    transaction_repository,
    warehouse_repository,
)
from database.exceptions import DATABASE_ERRORS
from shared import analytics, overview
from shared.formatting import format_int, local_time_text, month_abbr
from shared.currency import format_money

_FILTER_KEYS = (
    (overview.FILTER_ALL, "all"),
    (overview.FILTER_WAREHOUSES, "warehouses"),
    (overview.FILTER_DEALERSHIPS, "dealerships"),
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
        super().__init__(tr("page.overview.title"), parent, subtitle=tr("page.overview.subtitle"))
        self._today_provider = today_provider  # injectable so tests can pin "today"
        self._filter = overview.FILTER_ALL
        self._low_only = False
        self._query = ""
        self._products = []
        self._levels = []
        self._warehouses = []
        self._roster = []

        self._approvals_button = CompactButton(tr("header.pending_approvals"))
        self._approvals_button.setToolTip(tr("admin.overview.approvals_tip"))
        self._approvals_button.clicked.connect(self.open_purchase_requests.emit)
        self.add_header_action(self._approvals_button)

        refresh_button = CompactButton(tr("admin.refresh"))
        refresh_button.clicked.connect(self.reload)
        self.add_header_action(refresh_button)

        self.body_layout().addWidget(self._build_metric_row())
        self.body_layout().addWidget(self._build_stock_section(), stretch=1)
        self.body_layout().addWidget(self._build_activity_section())
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
        self._revenue_card = StatCard(tr("admin.overview.revenue_mtd"), "—")
        self._warehouse_card = StatCard(tr("admin.overview.card_warehouses"), "—")
        self._dealership_card = StatCard(tr("admin.overview.card_dealerships"), "—")
        for card in (self._revenue_card, self._warehouse_card, self._dealership_card):
            layout.addWidget(card, stretch=1)
        return row

    def _build_stock_section(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._stock_section = Section(tr("admin.overview.stock_kicker"), tr("admin.overview.stock_heading"))

        self._filter_group = QButtonGroup(self)
        self._filter_buttons: dict[str, SegmentButton] = {}
        for key, label_key in _FILTER_KEYS:
            button = SegmentButton(tr(f"admin.overview.{label_key}"))
            button.clicked.connect(lambda _c=False, k=key: self.set_location_filter(k))
            self._filter_group.addButton(button)
            self._filter_buttons[key] = button
            self._stock_section.add_header_control(button)
        self._filter_buttons[overview.FILTER_ALL].setChecked(True)

        self._low_only_checkbox = AnimatedCheckBox(tr("admin.overview.below_reorder"))
        self._low_only_checkbox.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        self._low_only_checkbox.toggled.connect(self.set_low_only)
        self._stock_section.add_header_control(self._low_only_checkbox)

        self._query_input = QLineEdit()
        self._query_input.setPlaceholderText(tr("admin.overview.filter_placeholder"))
        self._query_input.setMaximumWidth(180)
        self._query_input.textChanged.connect(self.set_query)
        self._stock_section.add_header_control(self._query_input)

        self._stock_table = styled_table([tr("admin.overview.col_sku"), tr("admin.overview.col_product")])
        self._stock_table.setMinimumHeight(300)
        self._stock_section.body_layout().addWidget(self._stock_table)

        self._stock_footer = QLabel("")
        self._stock_footer.setStyleSheet(f"color: {p['text_secondary']}; font-size: 11px; padding: 8px 16px;")
        self._stock_section.body_layout().addWidget(self._stock_footer)
        return self._stock_section

    def _build_activity_section(self) -> QWidget:
        self._activity_section = Section(tr("activity.overview_kicker"), tr("activity.overview_heading"))
        self._activity_table = activity_table()
        self._activity_table.setMinimumHeight(200)
        self._activity_section.body_layout().addWidget(self._activity_table)
        return self._activity_section

    def _build_attendance_section(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._attendance_section = Section(tr("admin.overview.att_kicker"), tr("admin.overview.att_heading"))
        self._attendance_summary = QLabel("")
        self._attendance_summary.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        self._attendance_section.add_header_control(self._attendance_summary)
        self._attendance_table = styled_table([tr(f"admin.overview.att_{key}") for key in ("employee", "site", "in", "out", "status")])
        self._attendance_table.setMinimumHeight(180)
        self._attendance_section.body_layout().addWidget(self._attendance_table)
        return self._attendance_section

    # --- state --------------------------------------------------------

    def set_pending_count(self, count: int) -> None:
        label = tr("header.pending_approvals")
        self._approvals_button.setText(f"{label}  {count}" if count else label)

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
            sale_cost_repository.attach_costs(sales)  # what those units cost when they were sold, for profit
            self._roster = attendance_repository.list_roster()
        except DATABASE_ERRORS:
            self._products, self._levels, self._warehouses, self._roster = [], [], [], []
            self._stock_footer.setText(tr("admin.overview.load_failed"))
            return
        self._render_revenue(period, sales, previous, dealerships)
        self._render_warehouses(overview.capacity_summary(self._warehouses, used))
        self._render_dealerships(
            overview.dealership_summary(dealerships, {t.dealership_code for t in sales if t.dealership_code}),
            analytics.sales_today(sales, today), on_the_road,
        )
        self._render_stock()
        self._render_activity()
        self._render_attendance()

    # --- rendering ----------------------------------------------------

    def _render_activity(self) -> None:
        try:
            events = activity_repository.list_recent(8, "info")
        except DATABASE_ERRORS:
            events = []
        fill_activity_table(self._activity_table, events)

    def _render_revenue(self, period, sales, previous, dealerships) -> None:
        revenue = analytics.revenue_between(sales, period.start, period.end)
        _, _, change = analytics.period_comparison(period, sales, previous)
        direction = analytics.change_direction(change)  # decided on the rounded figure; 0 = neutral
        self._revenue_card.set_value(format_money(revenue))
        self._revenue_card.set_trend(
            analytics.change_text(change) if change is not None else "",
            None if not direction else direction > 0,
        )
        self._revenue_card.set_corner_note(tr("admin.overview.vs_month").format(month=month_abbr(period.prev_start)) if change is not None else "")
        self._revenue_card.clear_footer()
        regions = [(r, v) for r, v in analytics.revenue_by_region(sales, dealerships).items() if v > 0]
        regions.sort(key=lambda pair: -pair[1])
        footer = self._revenue_card.footer_layout()
        if not regions:
            footer.addWidget(stat_breakdown_item(tr("admin.overview.this_month"), tr("admin.overview.no_sales_yet")))
        # Gross profit counts only the lines whose cost was known when they
        # were sold, and says how much of the revenue that leaves out.
        profit = analytics.profit_between(sales, period.start, period.end)
        if profit.has_profit:
            footer.addWidget(stat_breakdown_item(tr("admin.overview.gross_profit"), format_money(profit.profit)))
            footer.addWidget(stat_breakdown_item(tr("admin.overview.margin"), analytics.margin_display(profit.margin)))
        if profit.revenue_cents > 0 and profit.unknown_revenue_cents > 0:
            footer.addWidget(stat_breakdown_item(
                tr("admin.overview.cost_unknown"), tr("admin.overview.of_revenue").format(percent=profit.unknown_percent)))
        for region, value in regions[:2 if profit.revenue_cents > 0 else 3]:
            footer.addWidget(stat_breakdown_item(region_label(region), analytics.compact_amount(value)))

    def _render_warehouses(self, summary: overview.CapacitySummary) -> None:
        p = CLASSICAL_PALETTE
        self._warehouse_card.set_value("—" if summary.overall is None else f"{round(summary.overall * 100)}%")
        self._warehouse_card.set_trend(
            tr("admin.overview.capacity_used") if summary.overall is not None else tr("admin.overview.no_capacities"), None
        )
        self._warehouse_card.set_corner_note(tr("admin.overview.n_active").format(n=summary.active))
        self._warehouse_card.clear_footer()
        footer = self._warehouse_card.footer_layout()
        holder = QWidget()
        rows = QVBoxLayout(holder)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        if not summary.warehouses:
            note = QLabel(tr("admin.overview.no_warehouses"))
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
            tr("admin.overview.active_silent").format(n=summary.silent) if summary.silent else tr("admin.overview.active_all"),
            None,
        )
        regions = len(summary.by_region)
        self._dealership_card.set_corner_note(plural("admin.overview.regions", regions))
        self._dealership_card.clear_footer()
        footer = self._dealership_card.footer_layout()
        footer.addWidget(stat_breakdown_item(tr("admin.overview.sales_today"), str(today[0])))
        footer.addWidget(stat_breakdown_item(tr("admin.overview.revenue_today"), analytics.compact_amount(today[1])))
        footer.addWidget(stat_breakdown_item(tr("admin.overview.units_road"), format_int(on_road)))

    def _render_stock(self) -> None:
        grid = overview.stock_grid(
            self._products, self._levels, self._warehouses, self._filter, self._low_only, self._query
        )
        headers = [
            tr("admin.overview.col_sku"), tr("admin.overview.col_product"),
            *(enum_label("stock_col", name) for name in grid.columns),
            tr("admin.overview.col_total"), tr("admin.overview.col_reorder"), tr("admin.overview.col_status"),
        ]
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
                    cell(format_int(value), right=True, color=CLASSICAL_PALETTE["accent"] if zero else None),
                )
            total_col = first_number + len(row.cells)
            table.setItem(r, total_col, cell(format_int(row.total), right=True))
            table.setItem(r, total_col + 1, cell(format_int(row.reorder_at), right=True))
            status = cell(enum_label("stock_status", row.status))
            status.setForeground(QColor(_STATUS_COLORS[row.status]))
            table.setItem(r, total_col + 2, status)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        shown_note = "" if self._filter == overview.FILTER_ALL else tr("admin.overview.footer_totals_note")
        self._stock_footer.setText(
            tr("admin.overview.stock_footer").format(shown=len(grid.rows), total=len(self._products), note=shown_note)
        )

    def _render_attendance(self) -> None:
        today = overview.attendance_today(self._roster)
        self._attendance_summary.setText(
            tr("admin.overview.att_summary").format(
                on_site=today.on_site, checked_out=today.checked_out, not_in=today.not_in
            )
        )
        table = self._attendance_table
        table.setRowCount(len(today.rows))
        for r, entry in enumerate(today.rows):
            table.setItem(r, 0, cell(entry["name"]))
            table.setItem(r, 1, cell(entry["location_name"]))
            table.setItem(r, 2, cell(local_time_text(entry["check_in_at"]), right=True))
            table.setItem(r, 3, cell(local_time_text(entry["check_out_at"]), right=True))
            table.setItem(r, 4, cell(enum_label("attendance", entry["status"])))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
