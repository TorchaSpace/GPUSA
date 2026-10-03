"""Inline (non-overlay) banner shown in the cart view's content flow when
a scanned product is at/below its critical stock level.

TODO (next slice): simple QWidget using COLOR_ALERT_CRITICAL, inserted
into CartView's layout above the cart table - never as a floated popup
on top of the cart.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class StockAlertBanner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        raise NotImplementedError
