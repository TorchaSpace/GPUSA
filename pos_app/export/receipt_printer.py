"""Sends a built ReceiptDocument to the system's default printer.

The ONLY thing this module does is I/O - all formatting/layout is
already decided by shared.builders.receipt_builder.build_receipt(), so
the exact same builder output could also feed an on-screen preview.

Prints via Qt's QPrinter/QTextDocument (plain monospace text, one
physical page) rather than a raw ESC/POS byte stream - no specific
thermal printer model has been chosen yet (that's still TBD), and
QPrinter works against whatever's set as the OS default printer/PDF
printer in the meantime, so this is real printing today and can be
swapped for an ESC/POS driver later without touching build_receipt() or
checkout_service.py.

A failed *physical* print (no printer installed, driver error, etc.)
must never undo an already-finalized, already-paid sale - so failures
here are caught and logged, not raised. See checkout_service.py: by the
time this runs, finalize_transaction() has already committed.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from shared.builders.receipt_builder import ReceiptDocument
from shared.constants import FONT_FAMILY_MONOSPACE

logger = logging.getLogger(__name__)


def _receipt_font() -> QFont:
    """Consolas where it exists (Windows); otherwise the system's fixed-width
    font (Menlo on a Mac), so receipt columns still line up."""
    font = QFont(FONT_FAMILY_MONOSPACE, 10)
    if not QFontInfo(font).exactMatch():
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(10)
    return font


def print_receipt(receipt: ReceiptDocument) -> None:
    try:
        document = QTextDocument()
        document.setDefaultFont(_receipt_font())
        document.setPlainText("\n".join(receipt.lines))

        printer = QPrinter(QPrinter.PrinterResolution)
        document.print_(printer)
    except Exception:
        # Deliberately broad: any printing failure (missing printer,
        # driver error, headless/offscreen test environment) should
        # degrade to "the receipt didn't print" and nothing else - the
        # sale itself is already committed by the time this is called.
        logger.warning("Receipt printing failed; the sale was still completed.", exc_info=True)
