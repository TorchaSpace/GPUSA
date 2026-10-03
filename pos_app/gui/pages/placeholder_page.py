"""Generic "not built yet" screen, POS's equivalent of
admin_app/gui/pages/placeholder_page.py - themed (Organic palette) and
reachable from the nav so clicking it never crashes, but honest that
there's no real content behind it yet. Used for Receive Inventory (see
task list: needs a Shipment entity that doesn't exist in the database).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pos_app.theme import FONT_HEADING, ORGANIC_PALETTE


class PlaceholderPage(QWidget):
    def __init__(self, title: str, note: str, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.setStyleSheet(f"background-color: {p['background']};")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        heading = QLabel(title)
        heading.setAlignment(Qt.AlignHCenter)
        heading.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 34px; color: {p['text_primary']};")
        layout.addWidget(heading)

        subheading = QLabel("Not built yet")
        subheading.setAlignment(Qt.AlignHCenter)
        subheading.setStyleSheet(f"font-size: 16px; color: {p['text_secondary']};")
        layout.addWidget(subheading)

        body = QLabel(note)
        body.setAlignment(Qt.AlignHCenter)
        body.setWordWrap(True)
        body.setMaximumWidth(480)
        body.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(body, alignment=Qt.AlignHCenter)
