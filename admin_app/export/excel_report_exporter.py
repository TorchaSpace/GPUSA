"""Renders a built ReportDocument to an .xlsx file. Mirrors pdf_report_exporter.py."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from shared.builders.report_builder import NumericText, ReportDocument
from admin_app.export.letterhead import store_name
from shared.spreadsheet_safety import excel_text


def excel_value(value):
    """(cell value, number format or None) for one report cell.

    Only what the report builders mark as a number is written as one: a
    NumericText carries its own value and format (counts, amounts, margins,
    changes), and real ints/floats get a plain format. Every other string
    stays text - a product named "1907" or "0.50" must not turn into a
    number - and is written by _text_cell, which stores it as a string cell
    that Excel never evaluates (so "=HYPERLINK(...)" is shown, not run)."""
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
    return excel_text(value), None


def _put(sheet, row: int, column: int, value):
    """Write one cell; text is forced to a string cell (never a formula)."""
    value, number_format = excel_value(value)
    cell = sheet.cell(row=row, column=column, value=value)
    if isinstance(value, str):
        cell.data_type = "s"
    if number_format:
        cell.number_format = number_format
    return cell


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
    _put(sheet, row, 1, store_name()).font = Font(bold=True, size=14)
    row += 1
    _put(sheet, row, 1, report.title).font = Font(bold=True, size=12)
    row += 1
    _put(sheet, row, 1, f"Generated {report.generated_at:%Y-%m-%d %H:%M}")
    row += 2

    for section in report.sections:
        _put(sheet, row, 1, section.title).font = Font(bold=True)
        row += 1
        for section_row in section.rows:
            for col_index, value in enumerate(section_row, start=1):
                _put(sheet, row, col_index, value)
            row += 1
        row += 1  # blank line between sections

    for column_cells in sheet.columns:
        widest = max(
            (len(str(cell.value)) for cell in column_cells if cell.value is not None),
            default=8,
        )
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(widest + 2, 10), 40)

    workbook.save(str(output_path))
