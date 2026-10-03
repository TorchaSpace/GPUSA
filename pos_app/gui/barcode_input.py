"""Persistent text input that captures physical barcode-scanner input.

Lives inside the main window's normal content flow (not an overlay) and
stays focused so a cashier can scan continuously without clicking back
into the field between items.

TODO (next slice): QLineEdit subclass that emits a `barcode_scanned(str)`
signal on Enter (scanners send the barcode text followed by Enter), then
clears and re-focuses itself.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit


class BarcodeInput(QLineEdit):
    barcode_scanned = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        raise NotImplementedError
