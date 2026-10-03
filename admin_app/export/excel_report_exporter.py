"""Renders a built ReportDocument to an .xlsx file. Mirrors pdf_report_exporter.py."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from shared.builders.report_builder import ReportDocument
from admin_app.export.letterhead import store_name
from shared.spreadsheet_safety import safe_cell


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
                sheet.cell(row=row, column=col_index, value=safe_cell(value))
            row += 1
        row += 1  # blank line between sections

    for column_cells in sheet.columns:
        widest = max(
            (len(str(cell.value)) for cell in column_cells if cell.value is not None),
            default=8,
        )
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(widest + 2, 10), 40)

    workbook.save(str(output_path))
