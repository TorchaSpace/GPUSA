"""Checkable tab/segment button in the mockup's style: muted text, gold
text + gold underline when selected. Same look Treasury uses for its tabs
and status filter, as a reusable component for the pages that follow."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from admin_app.theme import CLASSICAL_PALETTE


class SegmentButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label.replace("&", "&&"), parent)  # "&" alone is a mnemonic marker
        p = CLASSICAL_PALETTE
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; color: {p['text_secondary']}; border: none;
                border-bottom: 2px solid transparent; padding: 6px 10px; font-size: 13px; }}
            QPushButton:hover {{ color: {p['text_primary']}; }}
            QPushButton:checked {{ color: {p['accent']}; border-bottom: 2px solid {p['accent']}; }}
            """
        )
