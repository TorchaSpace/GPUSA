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
from shared.gui_kit.motion import HoverTween, blend
from shared.textcase import upper

MIN_HEIGHT_PX = 44

_VARIANT_STYLES = {
    "primary": ("{text_primary}", "{background}", "1px solid {text_primary}"),  # solid black/ink
    "accent": ("#ffffff", "{accent}", "1px solid {accent}"),  # solid blue
    "ghost": ("{text_primary}", "transparent", "1px solid {text_primary}"),  # outlined
}


class IndustryButton(QPushButton):
    def __init__(self, label: str, variant: str = "primary", parent=None, height: int = MIN_HEIGHT_PX, font_px: int = 13):
        super().__init__(upper(label), parent)
        if variant not in _VARIANT_STYLES:
            raise ValueError(f"Unknown IndustryButton variant {variant!r}")
        p = INDUSTRY_PALETTE
        self.setMinimumHeight(height)
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
        self._look = (bg, hover_bg, fg, border, font_px)
        self._level = -1.0
        self._paint_level(0.0)
        # The fill glides to its hover colour instead of snapping.
        self._tween = HoverTween(self, self._paint_level)

    def _paint_level(self, level: float) -> None:
        if abs(level - self._level) < 0.03 and level not in (0.0, 1.0):
            return
        self._level = level
        p = INDUSTRY_PALETTE
        bg, hover_bg, fg, border, font_px = self._look
        fill = bg if bg == "transparent" and level == 0.0 else blend(p["background"] if bg == "transparent" else bg, hover_bg, level)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {fill};
                color: {fg};
                border: {border};
                border-radius: 0;
                padding: 0 20px;
                font-family: {FONT_HEADING_CSS};
                font-weight: 600;
                font-size: {font_px}px;
                letter-spacing: 1px;
            }}
            QPushButton:pressed {{ background-color: {blend(hover_bg, p['text_primary'], 0.25)}; }}
            QPushButton:disabled {{
                background-color: {p['surface']};
                color: {p['text_secondary']};
                border: 1px solid {p['border']};
            }}
            """
        )
