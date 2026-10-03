"""Small, dense button used throughout the Admin app's data-dense screens.

Deliberately NOT in shared/gui_kit/ - see gui_kit/__init__.py's docstring:
Admin favors information density (small, tightly-padded buttons) over
POS/Depot's large tap targets, so the STYLING here is Admin's own, even
though the underlying mechanism (a plain QPushButton) is identical
everywhere.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from admin_app.theme import CLASSICAL_PALETTE

# Admin-specific sizing - intentionally NOT in shared/constants.py, for
# the same reason pos_app/gui/components/action_button.py keeps its own.
MAX_HEIGHT_PX = 28
FONT_SIZE_PX = 12


class CompactButton(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        self.setMaximumHeight(MAX_HEIGHT_PX)
        self.setCursor(Qt.PointingHandCursor)
        # Pulls colors from admin_app's own CLASSICAL_PALETTE (see
        # admin_app/theme.py) rather than a hardcoded hex value or the
        # generic shared DARK_PALETTE, now that admin_app has its own
        # visual identity - the app only ever launches in dark mode
        # today, so this isn't mode-aware; revisit if a light-mode
        # toggle is ever added.
        p = CLASSICAL_PALETTE
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {p['surface_raised']};
                color: {p['text_primary']};
                border: 1px solid {p['border']};
                border-radius: {p['radius_sm']};
                padding: 2px 10px;
                font-size: {FONT_SIZE_PX}px;
                font-family: '{p['font_family']}';
            }}
            QPushButton:hover {{
                border-color: {p['accent']};
                color: {p['accent']};
            }}
            QPushButton:pressed {{
                background-color: {p['border']};
            }}
            QPushButton:disabled {{
                color: {p['text_secondary']};
            }}
            """
        )
