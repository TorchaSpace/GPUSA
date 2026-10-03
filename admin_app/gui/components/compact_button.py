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
MAX_HEIGHT_PX = 32
FONT_SIZE_PX = 12


class CompactButton(QPushButton):
    def __init__(self, label: str, parent=None, variant: str = "secondary"):
        """variant: "secondary" (outlined neutral, the default), "primary"
        (accent outline - the page's one main action) or "ghost" (text only)."""
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
        self.setStyleSheet(_style(p, variant))


def _style(p: dict, variant: str) -> str:
    if variant == "primary":
        colour, edge, hover_bg, press_bg = p["accent"], p["accent"], "rgba(225, 173, 102, 30)", "rgba(225, 173, 102, 55)"
    elif variant == "ghost":
        colour, edge, hover_bg, press_bg = p["accent"], "transparent", "rgba(225, 173, 102, 26)", "rgba(225, 173, 102, 48)"
    else:
        colour, edge, hover_bg, press_bg = p["text_primary"], p["border"], "rgba(234, 231, 231, 15)", "rgba(234, 231, 231, 30)"
    return f"""
        QPushButton {{
            background-color: transparent;
            color: {colour};
            border: 1px solid {edge};
            border-radius: {p['radius_md']};
            padding: 3px 12px;
            font-size: {FONT_SIZE_PX}px;
            font-family: {p['font_family_css']};
        }}
        QPushButton:hover {{ background-color: {hover_bg}; border-color: {p['accent']}; color: {p['accent']}; }}
        QPushButton:pressed {{ background-color: {press_bg}; }}
        QPushButton:focus {{ border-color: {p['accent']}; }}
        QPushButton:disabled {{ color: {p['text_secondary']}; border-color: {p['border']}; background-color: transparent; }}
    """
