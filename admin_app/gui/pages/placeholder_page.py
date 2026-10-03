"""Generic "not built yet" page: themed and navigable (so clicking the
nav item never crashes or shows a blank/broken screen) but honest that
there's no real content behind it yet.

Used for the 7 mockup screens whose data domain doesn't exist in the
database yet (Dealership, Employee/Workforce, Treasury ledger, Purchase
Order, Shipment/Distribution - see the task list's "visual shell first"
scope decision). Each of these gets replaced with a real implementation
later, one domain at a time, same incremental pattern as everything else
in this codebase - this class is what stands in until then.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE
from admin_app.gui.components.admin_page import AdminPage


class PlaceholderPage(AdminPage):
    def __init__(self, title: str, note: str, parent: QWidget | None = None):
        super().__init__(title, parent)
        p = CLASSICAL_PALETTE

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 60, 0, 60)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignHCenter)

        heading = QLabel(tr("admin.placeholder.title"))
        heading.setAlignment(Qt.AlignHCenter)
        heading.setStyleSheet(f"font-size: 16px; color: {p['text_secondary']};")
        layout.addWidget(heading)

        body = QLabel(note)
        body.setAlignment(Qt.AlignHCenter)
        body.setWordWrap(True)
        body.setMaximumWidth(480)
        body.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(body, alignment=Qt.AlignHCenter)

        self.body_layout().addWidget(container)
        self.body_layout().addStretch(1)
