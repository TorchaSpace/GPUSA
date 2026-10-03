"""Depot's buttons: square corners, uppercase Barlow Condensed labels,
letter-spacing - recreates the mockup's `.btn` variants (`.btn-primary`
solid black, `.btn-accent` solid blue, `.btn-ghost`/outlined) after the
DS's own `border-radius: 0` override (see depot_app/theme.py).

Own sizing constants rather than shared/constants.py, same rationale as
pos_app/gui/components/action_button.py and admin_app's compact_button.py
- this expresses depot_app's own ergonomics (a floor kiosk needs a
larger tap target than admin's compact desktop rows).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE

MIN_HEIGHT_PX = 44

_VARIANT_STYLES = {
    "primary": ("{text_primary}", "{background}", "1px solid {text_primary}"),  # solid black/ink
    "accent": ("#ffffff", "{accent}", "1px solid {accent}"),  # solid blue
    "ghost": ("{text_primary}", "transparent", "1px solid {text_primary}"),  # outlined
}


class IndustryButton(QPushButton):
    def __init__(self, label: str, variant: str = "primary", parent=None):
        super().__init__(label.upper(), parent)
        if variant not in _VARIANT_STYLES:
            raise ValueError(f"Unknown IndustryButton variant {variant!r}")
        p = INDUSTRY_PALETTE
        self.setMinimumHeight(MIN_HEIGHT_PX)
        self.setCursor(Qt.PointingHandCursor)

        fg_template, bg_template, border_template = _VARIANT_STYLES[variant]
        # variant == "primary" inverts fg/bg (dark surface, light text) -
        # everything else reads its colors straight from the palette.
        if variant == "primary":
            fg, bg, border = "#ffffff", p["text_primary"], f"1px solid {p['text_primary']}"
        else:
            fg = fg_template.format(**p)
            bg = bg_template.format(**p)
            border = border_template.format(**p)

        hover_bg = p["accent_900"] if variant == "primary" else p["accent"] if variant == "accent" else p["surface"]

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: {border};
                border-radius: 0;
                padding: 0 20px;
                font-family: {FONT_HEADING_CSS};
                font-weight: 600;
                font-size: 13px;
                letter-spacing: 1px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
            }}
            QPushButton:disabled {{
                background-color: {p['surface']};
                color: {p['text_secondary']};
                border: 1px solid {p['border']};
            }}
            """
        )
