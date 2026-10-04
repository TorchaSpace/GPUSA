"""The bundled TrueType font for PDF exports (Turkish letters need it: reportlab's
built-in Helvetica has no ğ, ş or İ). Falls back to Helvetica if the files are missing."""

from __future__ import annotations

from shared.fonts import report_font_files

REGULAR_NAME, BOLD_NAME = "GPUSA-Sans", "GPUSA-Sans-Bold"
FALLBACK = ("Helvetica", "Helvetica-Bold")


def register_fonts() -> tuple[str, str]:
    """(regular, bold) font names to draw with."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    files = report_font_files()
    if files is None:
        return FALLBACK
    try:
        registered = set(pdfmetrics.getRegisteredFontNames())
        if REGULAR_NAME not in registered:
            pdfmetrics.registerFont(TTFont(REGULAR_NAME, str(files[0])))
        if BOLD_NAME not in registered:
            pdfmetrics.registerFont(TTFont(BOLD_NAME, str(files[1])))
    except Exception:  # an unreadable font file: still produce a PDF
        return FALLBACK
    return REGULAR_NAME, BOLD_NAME
