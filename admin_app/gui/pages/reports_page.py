"""Reports page - the real build of Reports.dc.html ("Analytics & Reports"),
replacing its themed placeholder.

Recreated from the mockup:
- A period selector (Month / Quarter / Year to date) with "data through"
  and the export buttons.
- "Revenue trend": the period's revenue, its change against the same
  stretch of the previous period, a Cumulative / Daily switch, and a line
  chart with the previous period overlaid and a dashed projection of the
  close. Hovering the chart reads out that day.
- A donut breakdown of where the revenue came from, and the ranked "Top
  performing dealerships" with a 7-day sparkline each.

All real, from transaction_repository + dealership_repository; every
number is computed by shared.analytics.

Deliberately different from the mockup:
- The breakdown is by dealership REGION (plus "Unassigned"), not
  "Wholesale / Dealer / Direct": no sales channel is recorded anywhere in
  this system, but the dealership every sale came off is. See
  shared/analytics.py's docstring.
- The mockup's "August closed at $5.71M" / "September projected $6.02M"
  lines are figures from its own script; here the same sentence is built
  from the real comparison window and a straight-line projection, and is
  left out when there is nothing to compare or extrapolate.
- No currency symbol, as everywhere else in the app.

Export: CSV, PDF and Excel all render the one ReportDocument that
shared.analytics.build_period_report builds. The original per-product
Sales Reports tab is still reachable from the header button.
"""

from __future__ import annotations

import html
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from admin_app.export.csv_report_exporter import export_to_csv
from admin_app.export.excel_report_exporter import export_to_excel
from admin_app.export.pdf_report_exporter import export_to_pdf
from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.charts import DonutChart, RevenueChart, Sparkline
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.components.segment_button import SegmentButton
from shared.i18n import enum_label, plural, region_label, tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import dealership_repository, sale_cost_repository, transaction_repository
from database.exceptions import DATABASE_ERRORS
from shared import analytics
from shared.formatting import day_month_text, format_number
from shared.currency import format_money

_SEGMENT_COLORS = [CLASSICAL_PALETTE["accent"], "#9b9797", "#d7d3d3", "#605d5d", "#7d7979", "#b0acac"]
_MODES = ("cumulative", "daily")


class ReportsPage(AdminPage):
    def __init__(self, parent: QWidget | None = None, today_provider=date.today):
        super().__init__(tr("page.reports.title"), parent, subtitle=tr("page.reports.subtitle"))
        self._today_provider = today_provider  # injectable so tests can pin "today"
        self._period_key = "month"
        self._mode = "cumulative"
        self._view: analytics.ReportView | None = None
        self._document = None  # the ReportDocument the export buttons render
        self._period: analytics.Period | None = None  # the window captured by the last reload()
        self._transactions, self._previous, self._week, self._dealerships = [], [], [], []
        p = CLASSICAL_PALETTE

        self.body_layout().addWidget(self._build_toolbar())
        self.body_layout().addWidget(self._build_trend())

        lower = QWidget()
        lower_layout = QHBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(16)
        lower_layout.addWidget(self._build_breakdown(), stretch=1)
        lower_layout.addWidget(self._build_top(), stretch=1)
        self.body_layout().addWidget(lower)
        self.body_layout().addStretch(1)
        self._empty_note = QLabel("")
        self._empty_note.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        self.body_layout().addWidget(self._empty_note)

        self.reload()

    # --- building -----------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        p = CLASSICAL_PALETTE
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._period_group = QButtonGroup(self)
        self._period_buttons: dict[str, SegmentButton] = {}
        for key in analytics.PERIOD_KEYS:
            button = SegmentButton(enum_label("period", analytics.PERIOD_LABELS[key]))
            button.clicked.connect(lambda _c=False, k=key: self.set_period(k))
            self._period_group.addButton(button)
            self._period_buttons[key] = button
            row.addWidget(button)
        self._period_buttons["month"].setChecked(True)
        row.addStretch(1)

        self._through_label = QLabel("")
        self._through_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        row.addWidget(self._through_label)
        for text, handler in ((tr("admin.reports.export_csv"), self.export_csv), (tr("admin.reports.export_excel"), self.export_excel),
                              (tr("admin.reports.export_pdf"), self.export_pdf)):
            button = CompactButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        return bar

    def _build_trend(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._trend_section = Section(tr("admin.reports.trend_kicker"), tr("admin.reports.trend_heading"))
        body = self._trend_section.body_layout()

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(16, 12, 16, 0)
        head_layout.setSpacing(12)
        self._headline = QLabel("—")
        self._headline.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 40px; font-weight: 400; color: {p['text_primary']};"
        )
        head_layout.addWidget(self._headline)
        self._delta = QLabel("")
        self._delta.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        head_layout.addWidget(self._delta)
        head_layout.addStretch(1)

        self._mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, SegmentButton] = {}
        for key in _MODES:
            button = SegmentButton(tr(f"admin.reports.{key}"))
            button.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            self._mode_group.addButton(button)
            self._mode_buttons[key] = button
            head_layout.addWidget(button)
        self._mode_buttons["cumulative"].setChecked(True)
        body.addWidget(head)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px; padding: 2px 16px 0 16px;")
        body.addWidget(self._note)

        # Gross profit and margin of the period (only sold lines with a known
        # cost), and how much of the revenue that leaves out.
        self._profit_note = QLabel("")
        self._profit_note.setWordWrap(True)
        self._profit_note.setStyleSheet(f"color: {p['text_primary']}; font-size: 12px; padding: 2px 16px 0 16px;")
        body.addWidget(self._profit_note)

        self._readout = QLabel(" ")
        self._readout.setStyleSheet(f"color: {p['text_primary']}; font-size: 12px; padding: 6px 16px 0 16px;")
        body.addWidget(self._readout)

        self._chart = RevenueChart()
        self._chart.hovered.connect(self._on_chart_hover)
        chart_holder = QWidget()
        holder_layout = QVBoxLayout(chart_holder)
        holder_layout.setContentsMargins(16, 4, 16, 12)
        holder_layout.addWidget(self._chart)
        body.addWidget(chart_holder)

        legend = QLabel(
            f"<span style='color:{p['accent']}'>━</span> {tr('admin.reports.legend_this')} &nbsp;&nbsp; "
            f"<span style='color:{p['text_secondary']}'>━</span> {tr('admin.reports.legend_prev')} &nbsp;&nbsp; "
            f"<span style='color:{p['accent']}'>╌</span> {tr('admin.reports.legend_proj')}"
        )
        legend.setStyleSheet(f"color: {p['text_secondary']}; font-size: 11px; padding: 0 16px 12px 16px;")
        body.addWidget(legend)
        return self._trend_section

    def _build_breakdown(self) -> QWidget:
        p = CLASSICAL_PALETTE
        section = Section(tr("admin.reports.sources_kicker"), tr("admin.reports.sources_heading"))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(16)
        self._donut = DonutChart()
        layout.addWidget(self._donut)
        self._legend_host = QWidget()
        self._legend_layout = QVBoxLayout(self._legend_host)
        self._legend_layout.setContentsMargins(0, 0, 0, 0)
        self._legend_layout.setSpacing(8)
        layout.addWidget(self._legend_host, stretch=1)
        section.body_layout().addWidget(row)
        return section

    def _build_top(self) -> QWidget:
        section = Section(tr("admin.reports.top_kicker"), tr("admin.reports.top_heading"))
        self._top_host = QWidget()
        self._top_layout = QVBoxLayout(self._top_host)
        self._top_layout.setContentsMargins(16, 8, 16, 12)
        self._top_layout.setSpacing(4)
        section.body_layout().addWidget(self._top_host)
        return section

    # --- state --------------------------------------------------------

    def set_period(self, key: str) -> None:
        self._period_key = key
        self._period_buttons[key].setChecked(True)
        self.reload()

    def set_mode(self, key: str) -> None:
        self._mode = key
        self._mode_buttons[key].setChecked(True)
        if self._view is not None:
            self._render()  # same data, different series - no need to re-query

    def current_view(self) -> analytics.ReportView | None:
        return self._view

    # --- data ---------------------------------------------------------

    def reload(self) -> None:
        today = self._today_provider()
        period = analytics.period_for(self._period_key, today)
        try:
            transactions = transaction_repository.list_between(period.start_datetime, period.end_exclusive)
            previous = transaction_repository.list_between(period.prev_start_datetime, period.prev_end_exclusive)
            dealerships = dealership_repository.list_all()
            # Early in a month/quarter/year the 7-day sparklines reach back
            # before the period starts: fetch those days too (local datetimes).
            lead_in = (
                transaction_repository.list_between(period.week_start_datetime, period.start_datetime)
                if period.week_start < period.start else []
            )
            for sales in (transactions, previous, lead_in):  # what the units cost when sold, for profit
                sale_cost_repository.attach_costs(sales)
        except DATABASE_ERRORS as exc:
            self._view = None
            self._document = None
            self._period = None
            self._empty_note.setText(tr("admin.reports.load_failed").format(error=exc))
            return
        week = lead_in + transactions
        self._period = period
        self._transactions, self._previous, self._week, self._dealerships = transactions, previous, week, dealerships
        self._document = analytics.build_period_report(
            transactions, period, dealerships, previous=previous, week_transactions=week
        )
        now = datetime.now()
        self._through_label.setText(
            tr("admin.reports.data_through").format(when=f"{day_month_text(now)} {now:%Y, %H:%M}")
        )
        self._render()

    def _render(self) -> None:
        period = self._period  # as captured by reload(): never re-read "today" here, or the chart and the export could differ
        if period is None:
            return
        view = analytics.build_report_view(
            period, self._transactions, self._previous, self._dealerships, self._mode, week_transactions=self._week
        )
        self._view = view
        self._empty_note.setText("" if view.sale_count else tr("admin.reports.no_sales"))
        self._render_trend(view)
        self._render_breakdown(view)
        self._render_top(view)

    def _render_trend(self, view: analytics.ReportView) -> None:
        period = view.period
        self._trend_section.set_kicker(
            tr("admin.reports.kicker_period").format(
                label=enum_label("period", period.label), start=day_month_text(period.start),
                end=f"{day_month_text(period.end)} {period.end.year}",
            )
        )
        self._headline.setText(format_money(view.revenue))
        # The change compares equal day counts; when the previous period is
        # shorter (31 Mar vs February) say which days were compared.
        compared = tr("admin.reports.compared_days").format(n=period.comparable_days) if period.is_clamped else ""
        self._delta.setText(
            tr("admin.reports.vs_prev").format(
                change=analytics.change_text(view.change), start=day_month_text(period.prev_start),
                end=day_month_text(period.prev_end), compared=compared,
            )
            if view.change is not None else tr("admin.reports.no_prior")
        )
        parts = [plural("admin.reports.sales", view.sale_count)]
        if view.previous_revenue > 0:
            parts.append(tr("admin.reports.prev_note").format(amount=format_money(view.previous_revenue)))
        if view.projected_total is not None:
            parts.append(tr("admin.reports.proj_note").format(amount=format_money(view.projected_total)))
        self._note.setText(" · ".join(parts))
        self._profit_note.setText(self._profit_text(view))
        self._readout.setText(" ")
        self._chart.set_series(view.current, view.previous, view.projection, view.x_labels, view.total_points)

    @staticmethod
    def _profit_text(view: analytics.ReportView) -> str:
        profit = view.profit
        if profit.revenue_cents <= 0:
            return ""
        parts = []
        if profit.has_profit:
            parts.append(tr("admin.reports.profit_note").format(
                profit=format_money(profit.profit), margin=analytics.margin_display(profit.margin)))
        if profit.unknown_revenue_cents > 0:
            parts.append(tr("admin.reports.cost_unknown").format(percent=profit.unknown_percent))
        return " · ".join(parts)

    def _on_chart_hover(self, index: int) -> None:
        if self._view is None or index < 0:
            self._readout.setText(" ")
            return
        label, current, previous, projected = self._view.readout(index)
        text = label
        if current is not None:
            text += "  ·  " + (
                tr("admin.reports.readout_projected").format(amount=format_money(current)) if projected
                else format_money(current)
            )
        if previous is not None:
            text += "  ·  " + tr("admin.reports.readout_previous").format(amount=format_money(previous))
        self._readout.setText(text)

    def _render_breakdown(self, view: analytics.ReportView) -> None:
        p = CLASSICAL_PALETTE
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        segments = [
            (region_label(region), value, _SEGMENT_COLORS[i % len(_SEGMENT_COLORS)]) for i, (region, value, _) in enumerate(view.regions)
        ]
        self._donut.set_segments(segments)
        self._donut.set_highlight(-1)
        self._donut.set_center(
            tr("admin.reports.donut_total"), analytics.compact_amount(view.revenue), plural("admin.reports.regions", len(segments))
        )
        if not view.regions:
            empty = QLabel(tr("admin.reports.nothing_breakdown"))
            empty.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
            self._legend_layout.addWidget(empty)
        for (region, value, share), (_, _, color) in zip(view.regions, segments):
            line = QLabel(
                f"<span style='color:{color}'>●</span>&nbsp; {region}"
                f"<span style='color:{p['text_secondary']}'> &nbsp;{format_number(share, 1)}%</span>"
                f"<br><span style='font-size:13px'>{format_money(value)}</span>"
            )
            line.setStyleSheet(f"color: {p['text_primary']}; font-size: 13px;")
            self._legend_layout.addWidget(line)
        self._legend_layout.addStretch(1)

    def _render_top(self, view: analytics.ReportView) -> None:
        p = CLASSICAL_PALETTE
        while self._top_layout.count():
            item = self._top_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not view.top:
            empty = QLabel(tr("admin.reports.no_top"))
            empty.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px; padding: 8px 0;")
            self._top_layout.addWidget(empty)
            return
        for rank, entry in enumerate(view.top, start=1):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 4, 0, 4)
            layout.setSpacing(12)
            rank_label = QLabel(f"{rank:02d}")
            rank_label.setFixedWidth(26)
            rank_label.setStyleSheet(f"color: {p['accent'] if rank == 1 else '#7d7979'}; font-size: 12px;")
            layout.addWidget(rank_label)
            name = QLabel(f"{entry.name}<br><span style='color:{p['text_secondary']}; font-size:11px'>{region_label(entry.region)}</span>")
            name.setStyleSheet(f"color: {p['text_primary']}; font-size: 13px;")
            layout.addWidget(name, stretch=1)
            spark = Sparkline()
            spark.set_values(entry.week, up=entry.trend is None or entry.trend >= 0)
            layout.addWidget(spark)
            trend_color = p["text_primary"] if entry.trend is None or entry.trend >= 0 else p["alert_warning"]
            profit_line = (
                "<br><span style='color:%s; font-size:11px'>%s</span>" % (p["text_secondary"], html.escape(
                    tr("admin.reports.top_profit").format(
                        profit=format_money(entry.profit.profit), margin=analytics.margin_display(entry.profit.margin))))
                if entry.profit.has_profit else ""
            )
            figures = QLabel(
                f"{format_money(entry.revenue)}<br>"
                f"<span style='color:{trend_color}; font-size:11px'>{analytics.change_text(entry.trend)}</span>"
                f"{profit_line}"
            )
            figures.setAlignment(Qt.AlignRight)
            figures.setStyleSheet(f"color: {p['text_primary']}; font-size: 13px;")
            figures.setMinimumWidth(96)
            layout.addWidget(figures)
            self._top_layout.addWidget(row)

    # --- export -------------------------------------------------------

    def export_csv(self) -> None:
        self._export(export_to_csv, tr("admin.reports.csv_filter"), ".csv")

    def export_excel(self) -> None:
        self._export(export_to_excel, tr("admin.excel_file_filter"), ".xlsx")

    def export_pdf(self) -> None:
        self._export(export_to_pdf, tr("admin.pdf_file_filter"), ".pdf")

    def _export(self, exporter, file_filter: str, suffix: str) -> None:
        if self._document is None:
            QMessageBox.information(self, tr("admin.reports.nothing_export_title"), tr("admin.reports.nothing_export_body"))
            return
        path_str, _ = QFileDialog.getSaveFileName(self, tr("admin.reports.save_report"), "", file_filter)
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != suffix:
            path = path.with_name(path.name + suffix)  # "Report 2026.10.03" -> "Report 2026.10.03.csv"
            if path.exists() and QMessageBox.question(
                self, tr("admin.reports.replace_title"), tr("admin.reports.replace_body").format(name=path.name)
            ) != QMessageBox.Yes:
                return
        try:
            exporter(self._document, path)
        except Exception as exc:  # a failed export must say so, whatever the cause
            QMessageBox.warning(self, tr("admin.reports.export_failed"), str(exc))
            return
        QMessageBox.information(self, tr("admin.export_done_title"), tr("admin.export_done_body").format(path=path))
