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
    summary_headers,
    summary_rows,
)
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.components.industry_widgets import StatCell, industry_table, item, kicker
from depot_app.theme import INDUSTRY_PALETTE
from shared.builders.movement_report_builder import MovementReport, build_movement_report
from shared.formatting import to_db_timestamp
from shared.models import Warehouse


def period_bounds(start: date, end: date) -> tuple[str, str]:
    """[local midnight of start, local midnight after end) as db timestamps."""
    return (to_db_timestamp(datetime.combine(start, time())),
            to_db_timestamp(datetime.combine(end + timedelta(days=1), time())))


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


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
        layout.addWidget(kicker(f"Stock movements · {warehouse.site_label}"))

        bar = QHBoxLayout()
        date_style = (f"background-color: {p['surface_raised']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
                      f"padding: 5px 8px; font-size: 14px;")
        self.from_input = QDateEdit()
        self.to_input = QDateEdit()
        for edit in (self.from_input, self.to_input):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd.MM.yyyy")
            edit.setStyleSheet(date_style)
        bar.addWidget(QLabel("From"))
        bar.addWidget(self.from_input)
        bar.addWidget(QLabel("To"))
        bar.addWidget(self.to_input)
        for label, days in (("Today", 1), ("7 days", 7), ("30 days", 30)):
            quick = IndustryButton(label, variant="ghost")
            quick.clicked.connect(lambda _c=False, d=days: self.set_last_days(d))
            bar.addWidget(quick)
        run = IndustryButton("Run", variant="primary")
        run.clicked.connect(self.run)
        bar.addWidget(run)
        bar.addStretch(1)
        self.excel_button = IndustryButton("Export Excel", variant="ghost")
        self.excel_button.clicked.connect(lambda: self._export_dialog("xlsx"))
        self.pdf_button = IndustryButton("Export PDF", variant="ghost")
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
                      (("in", "Units in"), ("out", "Units out"), ("net", "Net change"), ("count", "Movements"))}
        for index, cell in enumerate(self.cells.values()):
            cells.addWidget(cell, 0, index)
        layout.addLayout(cells)

        layout.addWidget(kicker("By product"))
        self.summary_table = industry_table(["SKU", "Product"])
        self.summary_table.setMinimumHeight(170)
        layout.addWidget(self.summary_table, stretch=1)
        layout.addWidget(kicker("Every movement"))
        self.detail_table = industry_table(MOVEMENT_HEADERS)
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
            self.message.setText("“To” is before “From”.")
            return None
        since, until = period_bounds(start, end)
        try:
            movements = stock_repository.list_movements(limit=None, location=self.warehouse.location,
                                                        since=since, until=until)
        except DataAccessError as exc:
            self.message.setText(f"Couldn't read movements: {exc}")
            return None
        self.report = build_movement_report(movements, self.warehouse.site_label, start, end)
        self._render()
        return self.report

    def _render(self) -> None:
        r = self.report
        self.message.setText(f"{r.period_text} · {len(r.lines)} product{'s' if len(r.lines) != 1 else ''} moved"
                             if r.movements else f"{r.period_text} · no stock movements at this warehouse.")
        self.cells["in"].set(f"+{r.units_in:,}")
        self.cells["out"].set(f"−{r.units_out:,}")
        self.cells["net"].set(f"{r.net:+,}")
        self.cells["count"].set(f"{len(r.movements):,}")

        headers = summary_headers(r)
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
            raise ValueError("Run the report first.")
        (export_excel if fmt == "xlsx" else export_pdf)(self.report, path)
        return path

    def _export_dialog(self, fmt: str) -> None:
        if self.report is None:
            return
        start, end = self.period()
        suggested = f"movements_{self.warehouse.code}_{start:%Y%m%d}-{end:%Y%m%d}.{fmt}"
        filters = "Excel workbook (*.xlsx)" if fmt == "xlsx" else "PDF (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(self, "Export report", str(Path.home() / suggested), filters)
        if not path:
            return
        try:
            self.export_to(Path(path), fmt)
        except Exception as exc:  # disk full, file open in Excel, ...
            self.message.setText(f"Couldn't save: {exc}")
            return
        self.message.setText(f"Saved {path}")
