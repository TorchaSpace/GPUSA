"""Renders a built ReportDocument to an .xlsx file. Mirrors pdf_report_exporter.py."""

from __future__ import annotations

import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from shared.builders.report_builder import NumericText, ReportDocument
from admin_app.export.letterhead import store_name
from shared.spreadsheet_safety import safe_cell


_INTEGER = re.compile(r"^-?(0|[1-9]\d{0,14})$")  # no leading zeros: "007" stays text
_DECIMAL = re.compile(r"^-?(0|[1-9]\d{0,14})\.\d{1,6}$")
_PERCENT = re.compile(r"^[+-]?\d{1,9}(\.\d{1,6})?%$")


def excel_value(value, column: int = 1):
    """(cell value, number format or None) for one report cell.

    Numbers are written as real numbers: a NumericText carries its own
    value and format; a plain numeric-looking string in any column after
    the first ("12", "150.00", "+8.4%") is converted too. The first column
    is always text (labels, dates, barcodes - "0012345" must keep its
    zeros). Everything left as text goes through safe_cell, so a name
    like "=HYPERLINK(...)" cannot run as a formula."""
    if isinstance(value, NumericText):
        return value.number, value.number_format
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, int):
        return value, "#,##0"
    if isinstance(value, float):
        return value, "#,##0.00"
    if not isinstance(value, str):
        value = str(value)
    if column > 1:
        text = value.strip()
        if _INTEGER.match(text):
            return int(text), "#,##0"
        if _DECIMAL.match(text):
            return float(text), "#,##0." + "0" * len(text.split(".")[1])
        if _PERCENT.match(text):
            places = "0" * len(text.rstrip("%").partition(".")[2])
            body = "0." + places if places else "0"
            return float(text.rstrip("%")) / 100, f"+{body}%;-{body}%;{body}%"
    return safe_cell(value), None


def export_to_excel(report: ReportDocument, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales Report"

    row = 1
    # Letterhead - the one shared bit every export path (Excel here, PDF
    # in pdf_report_exporter.py) renders identically, per
    # architecture.md's "Exports fit their content" convention.
    sheet.cell(row=row, column=1, value=safe_cell(store_name())).font = Font(bold=True, size=14)
    row += 1
    sheet.cell(row=row, column=1, value=safe_cell(report.title)).font = Font(bold=True, size=12)
    row += 1
    sheet.cell(row=row, column=1, value=f"Generated {report.generated_at:%Y-%m-%d %H:%M}")
    row += 2

    for section in report.sections:
        sheet.cell(row=row, column=1, value=safe_cell(section.title)).font = Font(bold=True)
        row += 1
        for section_row in section.rows:
            for col_index, value in enumerate(section_row, start=1):
                number, number_format = excel_value(value, col_index)
                cell = sheet.cell(row=row, column=col_index, value=number)
                if number_format:
                    cell.number_format = number_format
            row += 1
        row += 1  # blank line between sections

    for column_cells in sheet.columns:
        widest = max(
            (len(str(cell.value)) for cell in column_cells if cell.value is not None),
            default=8,
        )
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(widest + 2, 10), 40)

    workbook.save(str(output_path))
