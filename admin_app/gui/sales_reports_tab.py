"""Sales reports tab: date-range picker + report preview + export buttons.

The preview table and both export formats all render from the exact same
ReportDocument (see shared.builders.report_builder.build_sales_report()
and architecture.md's "Builder reuse" convention) - Generate builds it
once and this tab holds onto it; Export PDF/Excel just hand that same
object to the matching exporter, never re-querying or re-aggregating.

First pass covers whole-day ranges (QDateEdit, not QDateTimeEdit) - good
enough for a manager picking "this week" / "this month"; a precise
time-of-day range can be added later if it's ever actually needed.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from pathlib import Path

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from admin_app.export.excel_report_exporter import export_to_excel
from admin_app.export.pdf_report_exporter import export_to_pdf
from admin_app.gui.components.compact_button import CompactButton
from database import transaction_repository
from database.exceptions import DATABASE_ERRORS
from shared.builders.report_builder import ReportDocument, build_sales_report
from shared.gui_kit.visual_tab import VisualTab
from shared.i18n import tr


class SalesReportsTab(VisualTab):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._start_input = QDateEdit(QDate.currentDate().addDays(-7))
        self._start_input.setCalendarPopup(True)
        self._end_input = QDateEdit(QDate.currentDate())
        self._end_input.setCalendarPopup(True)

        self._preview = QTableWidget()
        self._preview.setEditTriggers(QTableWidget.NoEditTriggers)
        self._preview.horizontalHeader().setVisible(False)
        self._preview.verticalHeader().setVisible(False)
        self.set_visual(self._preview)

        self._current_report: ReportDocument | None = None

        generate_button = CompactButton(tr("admin.generate_report"))
        generate_button.clicked.connect(self._generate)
        pdf_button = CompactButton(tr("admin.export_pdf"))
        pdf_button.clicked.connect(self._export_pdf)
        excel_button = CompactButton(tr("admin.export_excel"))
        excel_button.clicked.connect(self._export_excel)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(self._start_input)
        controls_layout.addWidget(self._end_input)
        controls_layout.addWidget(generate_button)
        controls_layout.addWidget(pdf_button)
        controls_layout.addWidget(excel_button)
        controls_layout.addStretch()
        self.set_controls(controls)

    def _selected_range(self) -> tuple[datetime, datetime]:
        # End is inclusive of the whole end day, hence the +1 day / start
        # of next day as the exclusive upper bound - matches
        # transaction_repository.list_between()'s [start, end) contract.
        start = datetime.combine(self._start_input.date().toPython(), time.min)
        end = datetime.combine(self._end_input.date().toPython(), time.min) + timedelta(days=1)
        return start, end

    def _generate(self) -> None:
        if self._start_input.date().toPython() > self._end_input.date().toPython():
            QMessageBox.warning(
                self, tr("admin.report_failed_title"), tr("admin.reports.bad_range")
            )
            return
        start, end = self._selected_range()
        try:
            transactions = transaction_repository.list_between(start, end)
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("admin.report_failed_title"), str(exc))
            return

        self._current_report = build_sales_report(transactions, start, end)
        self._render_preview(self._current_report)

    def _render_preview(self, report: ReportDocument) -> None:
        # Flatten every section into one simple table for the on-screen
        # glance: a section-title row, then its rows. The PDF/Excel
        # exports lay sections out properly - this preview only needs to
        # be readable, not print-quality.
        flattened_rows: list[tuple] = []
        max_columns = 1
        for section in report.sections:
            flattened_rows.append((section.title,))
            flattened_rows.extend(section.rows)
            if section.rows:
                max_columns = max(max_columns, max(len(row) for row in section.rows))

        self._preview.clear()
        self._preview.setRowCount(len(flattened_rows))
        self._preview.setColumnCount(max_columns)
        for row_index, row in enumerate(flattened_rows):
            for col_index in range(max_columns):
                value = row[col_index] if col_index < len(row) else ""
                self._preview.setItem(row_index, col_index, QTableWidgetItem(str(value)))
        self._preview.resizeColumnsToContents()

    def _export_pdf(self) -> None:
        self._export(export_to_pdf, tr("admin.pdf_file_filter"), ".pdf")

    def _export_excel(self) -> None:
        self._export(export_to_excel, tr("admin.excel_file_filter"), ".xlsx")

    def _export(self, exporter, file_filter: str, suffix: str) -> None:
        if self._current_report is None:
            QMessageBox.information(self, tr("admin.no_report_title"), tr("admin.no_report_body"))
            return

        path_str, _ = QFileDialog.getSaveFileName(self, tr("common.save"), "", file_filter)
        if not path_str:
            return
        path = Path(path_str)
        if path.suffix.lower() != suffix:
            # Append, never replace: "Report 2026.10.03" -> "Report 2026.10.03.pdf"
            # (with_suffix would turn it into "Report 2026.10.pdf"). The file
            # dialog only vetted the name as typed, so ask before overwriting.
            path = path.with_name(path.name + suffix)
            if path.exists() and QMessageBox.question(
                self, tr("admin.reports.replace_title"), tr("admin.reports.replace_body").format(name=path.name)
            ) != QMessageBox.Yes:
                return

        try:
            exporter(self._current_report, path)
        except (OSError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.report_failed_title"), str(exc))
            return

        QMessageBox.information(
            self, tr("admin.export_done_title"), tr("admin.export_done_body").format(path=str(path))
        )
