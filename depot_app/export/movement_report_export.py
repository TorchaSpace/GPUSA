"""Save a movement report (shared/builders/movement_report_builder.py) as
Excel (openpyxl: a Summary sheet per product and a Movements sheet with
every row) or PDF (reportlab: the summary table, then the movements)."""

from __future__ import annotations

from pathlib import Path

from shared import i18n
from shared.builders.movement_report_builder import MovementReport
from shared.i18n import tr
from shared.formatting import local_datetime_text
from shared.spreadsheet_safety import safe_cell
from shared.warehousing import direction_label, reason_label, reference_text

MOVEMENT_HEADERS = ["Time", "Movement", "SKU", "Product", "Qty", "Why", "Reference", "Handled by"]


def _header(english: str) -> str:
    """A column header in the current language (the English text is the id)."""
    key = f"depot.rep.h.{english}"
    text = tr(key)
    return english if text == key else text


def movement_headers() -> list[str]:
    return [_header(h) for h in MOVEMENT_HEADERS]


def movement_row(m: dict) -> list:
    sign = 1 if m["movement_type"] == "receive" else -1
    return [local_datetime_text(m["created_at"]), direction_label(m), m["barcode"], m["product_name"],
            sign * int(m["quantity"]), reason_label(m), reference_text(m), m.get("handled_by") or "—"]


def summary_headers(report: MovementReport) -> list[str]:
    return [_header(h) for h in ("SKU", "Product", "In", "Out", "Net")] + [
        tr(f"depot.rep.kind.{key}") if f"depot.rep.kind.{key}" in i18n._STRINGS["en"] else label
        for key, label in report.kinds_present
    ]


def summary_rows(report: MovementReport) -> list[list]:
    kinds = report.kinds_present
    rows = [[line.barcode, line.name, line.units_in, line.units_out, line.net]
            + [line.by_kind.get(key, 0) for key, _label in kinds] for line in report.lines]
    rows.append(["", tr("depot.rep.total"), report.units_in, report.units_out, report.net]
                + [report.kind_total(key) for key, _label in kinds])
    return rows


def export_excel(report: MovementReport, path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    sheet = book.active
    sheet.title = tr("depot.rep.sheet_summary")
    sheet.append([safe_cell(report.title)])
    sheet["A1"].font = Font(bold=True, size=14)
    sheet.append([report.period_text])
    sheet.append([])
    sheet.append(summary_headers(report))
    for cell in sheet[4]:
        cell.font = Font(bold=True)
    for row in summary_rows(report):
        sheet.append([safe_cell(v) for v in row])
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
    sheet.column_dimensions["A"].width = 14
    sheet.column_dimensions["B"].width = 32

    detail = book.create_sheet(tr("depot.rep.sheet_movements"))
    detail.append(movement_headers())
    for cell in detail[1]:
        cell.font = Font(bold=True)
    for m in report.movements:
        detail.append([safe_cell(v) for v in movement_row(m)])
    for column, width in zip("ABCDEFGH", (18, 11, 14, 30, 8, 18, 40, 24)):
        detail.column_dimensions[column].width = width
    book.save(str(path))


def export_pdf(report: MovementReport, path: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from shared.pdf_fonts import register_fonts

    regular, bold = register_fonts()
    styles = getSampleStyleSheet()
    for named in styles.byName.values():  # Title / Heading are bold, the rest regular (Turkish needs the TTF)
        named.fontName = bold if named.name.startswith(("Title", "Heading")) else regular
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm, title=report.title)
    style = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("FONTNAME", (0, 0), (-1, 0), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9e9ea")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#5980a6")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d4d4d7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])
    story = [
        Paragraph(report.title, styles["Title"]),
        Paragraph(
            tr("depot.rep.pdf_summary").format(
                period=report.period_text, units_in=f"{report.units_in:,}", units_out=f"{report.units_out:,}",
                net=f"{report.net:+,}", count=f"{len(report.movements):,}",
            ),
            styles["Normal"],
        ),
        Spacer(1, 0.4 * cm),
    ]
    if report.lines:
        summary = Table([summary_headers(report)] + summary_rows(report), repeatRows=1)
        summary.setStyle(style)
        summary.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), bold)]))
        story += [summary, Spacer(1, 0.6 * cm), Paragraph(tr("depot.rep.sheet_movements"), styles["Heading2"])]
        cell = styles["Normal"].clone("cell", fontSize=7, leading=8)
        rows = [movement_headers()] + [
            [Paragraph(str(v), cell) if i in (3, 6) else v for i, v in enumerate(movement_row(m))]
            for m in report.movements
        ]
        detail = Table(rows, repeatRows=1, colWidths=[2.8 * cm, 1.8 * cm, 2.4 * cm, 5.2 * cm, 1.2 * cm, 3 * cm, 6.5 * cm, 3.6 * cm])
        detail.setStyle(style)
        story.append(detail)
    else:
        story.append(Paragraph(tr("depot.rep.pdf_none"), styles["Normal"]))
    doc.build(story)
