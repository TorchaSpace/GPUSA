"""Generic "not tracked yet" content for every depot_app domain that has
no backend today (multi-warehouse/bins, staff attendance/badges,
purchasing/suppliers, treasury/ledger, Console's inert Dashboard/
Inventory/Shipments/Reports nav items) - same honesty pattern as
admin_app/gui/pages/placeholder_page.py, adapted to two shapes:

- PlaceholderPanel: a compact, blueprint-framed block to embed INSIDE an
  otherwise-real screen (e.g. the Floor kiosk's Check-in Log aside sits
  next to the real Inbound/Outbound panels).
- PlaceholderPage: a full standalone page (own title header) for a whole
  Console sidebar destination that has nothing real behind it at all.

Per the "build the entire UI visually exactly as it appears in the
mockup" scope decision: these are deliberately still themed with the
Industry palette/blueprint corners, not a bare "TODO" label - the visual
layout stays intact for when each domain gets real data later.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE


class PlaceholderPanel(BlueprintFrame):
    def __init__(self, title: str, note: str, parent: QWidget | None = None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        # Scoped by object name - a selector-less rule cascades to the
        # labels inside and draws a box around each of them.
        self.setObjectName("placeholderPanel")
        self.setStyleSheet(f"#placeholderPanel {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 40, 20, 40)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignHCenter)

        heading = QLabel(title.upper())
        heading.setAlignment(Qt.AlignHCenter)
        heading.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; letter-spacing: 1px; "
            f"font-size: 13px; color: {p['text_secondary']};"
        )
        layout.addWidget(heading)

        body = QLabel(note)
        body.setAlignment(Qt.AlignHCenter)
        body.setWordWrap(True)
        body.setMaximumWidth(420)
        body.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(body, alignment=Qt.AlignHCenter)


class PlaceholderPage(QWidget):
    """A full Console sidebar destination with nothing real behind it -
    own title header + a centered PlaceholderPanel body."""

    def __init__(self, title: str, note: str, parent: QWidget | None = None):
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self.setObjectName("placeholderPage")
        self.setStyleSheet(f"#placeholderPage {{ background-color: {p['background']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # No title of its own: the Console header above shows the page name.

        layout.addWidget(PlaceholderPanel("Coming soon", note), stretch=1)
