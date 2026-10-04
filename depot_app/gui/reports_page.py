"""Console > Reports - the stock-movement report for this warehouse (Erol's
choice for this page), replacing the themed placeholder.

Pick a period (From / To, or Today / 7 days / 30 days) and Run: four stat
cells (units in, units out, net, number of movements), a per-product
summary (in, out, net, and split by kind - receipts, written out, shipped
out, returned, transfers, counts; only kinds that occurred get a column),
and every movement in the period with who handled it. Export saves the
same report as Excel (Summary + Movements sheets) or PDF.

The numbers come from shared/builders/movement_report_builder.py, so the
screen and both exports can't disagree. Dates are local days; the period
includes both ends.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from database import stock_repository
from database.exceptions import DataAccessError
from depot_app.export.movement_report_export import (
    MOVEMENT_HEADERS,
    movement_row,
    export_excel,
    export_pdf,
    summary_rows,
)
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.components.industry_widgets import StatCell, industry_table, item, kicker
from depot_app.theme import INDUSTRY_PALETTE
from shared.builders.movement_report_builder import MovementReport, build_movement_report
from shared.formatting import to_db_timestamp
from shared.i18n import tr
from shared.models import Warehouse


def period_bounds(start: date, end: date) -> tuple[str, str]:
    """[local midnight of start, local midnight after end) as db timestamps."""
    return (to_db_timestamp(datetime.combine(start, time())),
            to_db_timestamp(datetime.combine(end + timedelta(days=1), time())))


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _header(english: str) -> str:
    """Column header text in the current language (the export modules keep English ids)."""
    key = f"depot.rep.h.{english}"
    text = tr(key)
    return english if text == key else text


def _summary_headers(report: MovementReport) -> list[str]:
    base = [_header(h) for h in ("SKU", "Product", "In", "Out", "Net")]
    return base + [tr(f"depot.rep.kind.{key}") for key, _label in report.kinds_present]


class ReportsPage(QWidget):
    def __init__(self, warehouse: Warehouse, parent: QWidget | None = None, today_provider=date.today):
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self.warehouse = warehouse
        self._today = today_provider
        self.report: MovementReport | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(kicker(tr("depot.rep.kicker").format(site=warehouse.site_label)))

        bar = QHBoxLayout()
        date_style = (f"background-color: {p['surface_raised']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
                      f"padding: 5px 8px; font-size: 14px;")
        self.from_input = QDateEdit()
        self.to_input = QDateEdit()
        for edit in (self.from_input, self.to_input):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd.MM.yyyy")
            edit.setStyleSheet(date_style)
        bar.addWidget(QLabel(tr("depot.rep.from")))
        bar.addWidget(self.from_input)
        bar.addWidget(QLabel(tr("depot.rep.to")))
        bar.addWidget(self.to_input)
        for label, days in ((tr("depot.rep.today"), 1), (tr("depot.rep.days7"), 7), (tr("depot.rep.days30"), 30)):
            quick = IndustryButton(label, variant="ghost")
            quick.clicked.connect(lambda _c=False, d=days: self.set_last_days(d))
            bar.addWidget(quick)
        run = IndustryButton(tr("depot.rep.run"), variant="primary")
        run.clicked.connect(self.run)
        bar.addWidget(run)
        bar.addStretch(1)
        self.excel_button = IndustryButton(tr("depot.rep.export_excel"), variant="ghost")
        self.excel_button.clicked.connect(lambda: self._export_dialog("xlsx"))
        self.pdf_button = IndustryButton(tr("depot.rep.export_pdf"), variant="ghost")
        self.pdf_button.clicked.connect(lambda: self._export_dialog("pdf"))
        bar.addWidget(self.excel_button)
        bar.addWidget(self.pdf_button)
        layout.addLayout(bar)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self.message)

        cells = QGridLayout()
        cells.setSpacing(12)
        self.cells = {key: StatCell(caption) for key, caption in
                      (("in", tr("depot.rep.units_in")), ("out", tr("depot.rep.units_out")),
                       ("net", tr("depot.rep.net")), ("count", tr("depot.rep.movements")))}
        for index, cell in enumerate(self.cells.values()):
            cells.addWidget(cell, 0, index)
        layout.addLayout(cells)

        layout.addWidget(kicker(tr("depot.rep.by_product")))
        self.summary_table = industry_table([_header("SKU"), _header("Product")])
        self.summary_table.setMinimumHeight(170)
        layout.addWidget(self.summary_table, stretch=1)
        layout.addWidget(kicker(tr("depot.rep.every_movement")))
        self.detail_table = industry_table([_header(h) for h in MOVEMENT_HEADERS])
        header = self.detail_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self.detail_table.setMinimumHeight(170)
        layout.addWidget(self.detail_table, stretch=1)

        self.set_last_days(7, run=False)
        self._sync()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.run()

    def set_last_days(self, days: int, run: bool = True) -> None:
        today = self._today()
        self.from_input.setDate(_qdate(today - timedelta(days=days - 1)))
        self.to_input.setDate(_qdate(today))
        if run:
            self.run()

    def period(self) -> tuple[date, date]:
        return self.from_input.date().toPython(), self.to_input.date().toPython()

    def run(self) -> MovementReport | None:
        start, end = self.period()
        if end < start:
            self.message.setText(tr("depot.rep.to_before_from"))
            return None
        since, until = period_bounds(start, end)
        try:
            movements = stock_repository.list_movements(limit=None, location=self.warehouse.location,
                                                        since=since, until=until)
        except DataAccessError as exc:
            self.message.setText(tr("depot.rep.read_failed").format(error=exc))
            return None
        self.report = build_movement_report(movements, self.warehouse.site_label, start, end)
        self._render()
        return self.report

    def _render(self) -> None:
        r = self.report
        n = len(r.lines)
        self.message.setText(tr("depot.rep.moved_one" if n == 1 else "depot.rep.moved_other").format(period=r.period_text, n=n)
                             if r.movements else tr("depot.rep.none").format(period=r.period_text))
        self.cells["in"].set(f"+{r.units_in:,}")
        self.cells["out"].set(f"−{r.units_out:,}")
        self.cells["net"].set(f"{r.net:+,}")
        self.cells["count"].set(f"{len(r.movements):,}")

        headers = _summary_headers(r)
        rows = summary_rows(r) if r.lines else []
        table = self.summary_table
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for c, value in enumerate(row):
                text = f"{value:+,}" if c == 4 and isinstance(value, int) else (f"{value:,}" if isinstance(value, int) else value)
                if c == 1 and value == "Total":
                    text = tr("depot.rep.total")
                table.setItem(i, c, item(text, right=c >= 2))

        self.detail_table.setRowCount(len(r.movements))
        for i, m in enumerate(r.movements):
            for c, value in enumerate(movement_row(m)):
                text = f"{value:+,}" if c == 4 else value
                self.detail_table.setItem(i, c, item(text, right=c == 4))
        self._sync()

    def _sync(self) -> None:
        has = self.report is not None and bool(self.report.movements)
        self.excel_button.setEnabled(has)
        self.pdf_button.setEnabled(has)

    def export_to(self, path: Path, fmt: str) -> Path:
        if self.report is None:
            raise ValueError(tr("depot.rep.run_first"))
        (export_excel if fmt == "xlsx" else export_pdf)(self.report, path)
        return path

    def _export_dialog(self, fmt: str) -> None:
        if self.report is None:
            return
        start, end = self.period()
        suggested = f"movements_{self.warehouse.code}_{start:%Y%m%d}-{end:%Y%m%d}.{fmt}"
        filters = tr("depot.rep.filter_xlsx") if fmt == "xlsx" else tr("depot.rep.filter_pdf")
        path, _ = QFileDialog.getSaveFileName(self, tr("depot.rep.export_title"), str(Path.home() / suggested), filters)
        if not path:
            return
        try:
            self.export_to(Path(path), fmt)
        except Exception as exc:  # disk full, file open in Excel, ...
            self.message.setText(tr("depot.rep.save_failed").format(error=exc))
            return
        self.message.setText(tr("depot.rep.saved").format(path=path))
