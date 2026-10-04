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

from admin_app.gui.motion import HoverTween, blend
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
        self._variant = variant
        self._level = -1.0
        self._apply_level(0.0)
        # Border and text warm up smoothly instead of snapping on hover.
        self._tween = HoverTween(self, self._apply_level)

    def _apply_level(self, level: float) -> None:
        if abs(level - self._level) < 0.02 and level not in (0.0, 1.0):
            return  # skip restyling for imperceptible steps
        self._level = level
        self.setStyleSheet(_style(CLASSICAL_PALETTE, self._variant, level))


def _style(p: dict, variant: str, level: float = 0.0) -> str:
    if variant == "primary":
        colour, edge, hover_bg, press_bg = p["accent"], p["accent"], "rgba(225, 173, 102, 30)", "rgba(225, 173, 102, 55)"
    elif variant == "ghost":
        colour, edge, hover_bg, press_bg = p["accent"], "transparent", "rgba(225, 173, 102, 26)", "rgba(225, 173, 102, 48)"
    else:
        colour, edge, hover_bg, press_bg = p["text_primary"], p["border"], "rgba(234, 231, 231, 15)", "rgba(234, 231, 231, 30)"
    if edge != "transparent":
        edge = blend(edge, p["accent"], level)
    if variant != "primary" and variant != "ghost":
        colour = blend(colour, p["accent"], level)
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
        QPushButton:hover {{ background-color: {hover_bg}; }}
        QPushButton:pressed {{ background-color: {press_bg}; }}
        QPushButton:focus {{ border-color: {p['accent']}; }}
        QPushButton:disabled {{ color: {p['text_secondary']}; border-color: {p['border']}; background-color: transparent; }}
    """
