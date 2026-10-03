"""Large, high-contrast, fast-tap button used for primary POS actions
(e.g. "Card"/"Cash" on the checkout panel). Sized for speed and
visibility, not density - the opposite ergonomic goal from admin_app's
CompactButton, per this file's original docstring.

Two variants match the mockup's payment buttons: "accent" (terracotta,
the Card button) and "dark" (near-black, the Cash button) - both pill-
shaped per pos_app's Organic theme's large corner radii.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from pos_app.theme import ORGANIC_PALETTE

# POS-specific sizing - intentionally NOT in shared/constants.py, since
# these values express POS's ergonomics, not a shared design token.
MIN_HEIGHT_PX = 64
FONT_SIZE_PX = 19


class ActionButton(QPushButton):
    def __init__(self, label: str, parent=None, variant: str = "accent"):
        super().__init__(label, parent)
        self.setMinimumHeight(MIN_HEIGHT_PX)
        self.setCursor(Qt.PointingHandCursor)

        p = ORGANIC_PALETTE
        if variant == "dark":
            bg, bg_hover, fg = p["text_primary"], "#474238", p["background"]
        else:
            bg, bg_hover, fg = p["accent"], "#d67f48", "#ffffff"

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: {MIN_HEIGHT_PX // 2}px;
                font-family: {p['font_family_css']};
                font-size: {FONT_SIZE_PX}px;
                font-weight: 700;
                padding: 0 24px;
            }}
            QPushButton:hover {{
                background-color: {bg_hover};
            }}
            QPushButton:disabled {{
                background-color: {p['border']};
                color: {p['text_secondary']};
            }}
            """
        )
