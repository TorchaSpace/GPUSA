"""Renders a built ReportDocument to a PDF file.

I/O and layout-to-PDF only - the report's content/aggregation is already
decided by shared.builders.report_builder.build_sales_report(). Page
size follows REPORT_PAGE_SIZE from shared/constants.py (both are "A4" -
reportlab's pagesizes.A4 constant is used directly here rather than
parsing the string, since this is the only exporter that needs an actual
reportlab page-size object).

Text is set in a bundled TrueType font (DejaVu Sans, shared/assets/fonts):
the built-in Helvetica has no Ş ş İ ı Ğ ğ and silently drops them. If the
font files are missing the exporter falls back to Helvetica rather than
failing the export.

Layout: columns are sized to their content; the first column and any
text column are left-aligned, columns of numbers/percentages right-aligned.
When a row would be wider than the page the widest column is narrowed and
its cells cut with an ellipsis, so nothing runs off the page.
"""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from shared.builders.report_builder import ReportDocument
from shared.fonts import report_font_files
from admin_app.export.letterhead import store_name

_MARGIN = 2 * cm
_LINE_HEIGHT = 14
_REGULAR_NAME, _BOLD_NAME = "GPUSA-Sans", "GPUSA-Sans-Bold"
_FALLBACK = ("Helvetica", "Helvetica-Bold")
_FONT_SIZE = 9
_COLUMN_GAP = 8


def register_fonts() -> tuple[str, str]:
    """(regular, bold) font names to draw with: the bundled TTFs registered
    with reportlab (once), or Helvetica when they are not on disk."""
    files = report_font_files()
    if files is None:
        return _FALLBACK
    try:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if _REGULAR_NAME not in registered:
            pdfmetrics.registerFont(TTFont(_REGULAR_NAME, str(files[0])))
        if _BOLD_NAME not in registered:
            pdfmetrics.registerFont(TTFont(_BOLD_NAME, str(files[1])))
    except Exception:  # unreadable/corrupt font file: still produce a PDF
        return _FALLBACK
    return _REGULAR_NAME, _BOLD_NAME


def fit_text(text: str, font: str, size: float, max_width: float) -> str:
    """`text` cut with a trailing "…" (or "...") so it fits `max_width`."""
    text = str(text).replace("\n", " ")
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "..." if font in _FALLBACK else "…"
    while text and pdfmetrics.stringWidth(text + ellipsis, font, size) > max_width:
        text = text[:-1]
    return text.rstrip() + ellipsis


_NUMBERISH = re.compile(r"^[+-]?[\d.,]+%?$")


def _column_layout(rows, font: str, usable: float) -> tuple[list[float], set[int]]:
    """Column widths that fit `usable` (the widest column gives way first
    when the rows are too wide) and the set of right-aligned columns: every
    column after the first whose cells are all numbers/percentages."""
    columns = max((len(row) for row in rows), default=0)
    widths = [
        max((pdfmetrics.stringWidth(str(row[i]), font, _FONT_SIZE) for row in rows if i < len(row)), default=0.0)
        for i in range(columns)
    ]
    excess = sum(widths) + _COLUMN_GAP * max(columns - 1, 0) - usable
    while excess > 0.01:
        widest = max(range(columns), key=lambda i: widths[i])
        cut = min(excess, max(widths[widest] - 30, 0))
        if cut <= 0:
            break
        widths[widest] -= cut
        excess -= cut
    right = {
        i for i in range(1, columns)
        if all(i >= len(row) or _NUMBERISH.match(str(row[i])) for row in rows)
    }
    return widths, right


def export_to_pdf(report: ReportDocument, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    regular, bold = register_fonts()
    page_width, page_height = A4
    usable = page_width - 2 * _MARGIN
    pdf = canvas.Canvas(str(output_path), pagesize=A4)

    def new_page() -> float:
        pdf.showPage()
        return page_height - _MARGIN

    def line(text: str, font: str, size: float, y: float) -> None:
        pdf.setFont(font, size)
        pdf.drawString(_MARGIN, y, fit_text(text, font, size, usable))

    y = page_height - _MARGIN

    # Letterhead - the one shared bit every export path (PDF here, Excel
    # in excel_report_exporter.py) renders identically, per
    # architecture.md's "Exports fit their content" convention.
    line(store_name(), bold, 14, y)
    y -= _LINE_HEIGHT * 1.5

    line(report.title, bold, 12, y)
    y -= _LINE_HEIGHT

    line(f"Generated {report.generated_at:%Y-%m-%d %H:%M}", regular, 9, y)
    y -= _LINE_HEIGHT * 1.5

    for section in report.sections:
        if y < _MARGIN + _LINE_HEIGHT * 3:
            y = new_page()

        line(section.title, bold, 11, y)
        y -= _LINE_HEIGHT

        widths, right = _column_layout(section.rows, regular, usable)
        for row in section.rows:
            if y < _MARGIN:
                y = new_page()
            pdf.setFont(regular, _FONT_SIZE)
            x = _MARGIN
            for index, cell in enumerate(row):
                width = widths[index] if index < len(widths) else 0
                text = fit_text(str(cell), regular, _FONT_SIZE, width) if width else ""
                if index in right:
                    pdf.drawRightString(x + width, y, text)
                else:
                    pdf.drawString(x, y, text)
                x += width + _COLUMN_GAP
            y -= _LINE_HEIGHT
        y -= _LINE_HEIGHT * 0.5

    pdf.save()
