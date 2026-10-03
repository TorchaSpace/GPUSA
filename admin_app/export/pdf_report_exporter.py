"""Renders a built ReportDocument to a PDF file.

I/O and layout-to-PDF only - the report's content/aggregation is already
decided by shared.builders.report_builder.build_sales_report(). Page
size follows REPORT_PAGE_SIZE from shared/constants.py (both are "A4" -
reportlab's pagesizes.A4 constant is used directly here rather than
parsing the string, since this is the only exporter that needs an actual
reportlab page-size object).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

from shared.builders.report_builder import ReportDocument
from admin_app.export.letterhead import store_name

_MARGIN = 2 * cm
_LINE_HEIGHT = 14


def export_to_pdf(report: ReportDocument, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    page_width, page_height = A4
    pdf = canvas.Canvas(str(output_path), pagesize=A4)

    def new_page() -> float:
        pdf.showPage()
        return page_height - _MARGIN

    y = page_height - _MARGIN

    # Letterhead - the one shared bit every export path (PDF here, Excel
    # in excel_report_exporter.py) renders identically, per
    # architecture.md's "Exports fit their content" convention.
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(_MARGIN, y, store_name())
    y -= _LINE_HEIGHT * 1.5

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(_MARGIN, y, report.title)
    y -= _LINE_HEIGHT

    pdf.setFont("Helvetica", 9)
    pdf.drawString(_MARGIN, y, f"Generated {report.generated_at:%Y-%m-%d %H:%M}")
    y -= _LINE_HEIGHT * 1.5

    for section in report.sections:
        if y < _MARGIN + _LINE_HEIGHT * 3:
            y = new_page()

        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(_MARGIN, y, section.title)
        y -= _LINE_HEIGHT

        pdf.setFont("Helvetica", 9)
        for row in section.rows:
            if y < _MARGIN:
                y = new_page()
                pdf.setFont("Helvetica", 9)
            pdf.drawString(_MARGIN, y, "    ".join(str(cell) for cell in row))
            y -= _LINE_HEIGHT
        y -= _LINE_HEIGHT * 0.5

    pdf.save()
