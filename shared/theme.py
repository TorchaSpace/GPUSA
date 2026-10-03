"""Palette dicts + the one function that applies them.

Minimalist, brutalist, dark-first: near-black/dark-gray surfaces,
high-contrast light text, no decorative padding, and accent colors used
ONLY for functional meaning (alerts, primary actions) - never as
decoration. All three apps call apply_theme() with their own root widget;
no module outside this file should hardcode a color/QSS value that
belongs to the base palette. Component-level sizing (button size, row
height) still lives in each app's own gui/components/ package - this
file only owns color and base typography.

Per-app visual identity (added when admin_app and pos_app each got a
distinct look from their own mockups - see admin_app/theme.py and
pos_app/theme.py): apply_theme() accepts an optional `palette` dict that
overrides DARK_PALETTE/LIGHT_PALETTE entirely. This keeps the MECHANISM
(one function, one QSS builder, one place that calls setStyleSheet) here
in shared/ per architecture.md's rule that only mechanics belong in
shared/, while letting each app own its actual colors/fonts in its own
module. A passed-in palette dict uses the same keys as DARK_PALETTE, plus
an optional "font_family" key (falls back to shared.constants.FONT_FAMILY
when absent) and an optional "radius_sm"/"radius_md"/"radius_lg" trio
(falls back to a flat 0px when absent, matching the original brutalist
look). depot_app hasn't been given its own palette yet, so it keeps
calling apply_theme(app, mode="dark") with no palette arg and gets the
original shared DARK_PALETTE unchanged.
"""

from __future__ import annotations

from shared.constants import COLOR_ALERT_CRITICAL, COLOR_ALERT_SUCCESS, FONT_FAMILY
from shared.fonts import css_font_stack

DARK_PALETTE = {
    "background": "#121212",
    "surface": "#1E1E1E",
    "surface_raised": "#262626",
    "border": "#333333",
    "text_primary": "#F2F2F2",
    "text_secondary": "#A0A0A0",
    "alert_critical": COLOR_ALERT_CRITICAL,
    "alert_success": COLOR_ALERT_SUCCESS,
}

LIGHT_PALETTE = {
    "background": "#F5F5F5",
    "surface": "#FFFFFF",
    "surface_raised": "#EDEDED",
    "border": "#D0D0D0",
    "text_primary": "#161616",
    "text_secondary": "#5A5A5A",
    "alert_critical": COLOR_ALERT_CRITICAL,
    "alert_success": COLOR_ALERT_SUCCESS,
}


def _build_qss(palette: dict[str, str]) -> str:
    """Turn a palette dict into a base QSS stylesheet.

    This covers only the generic/base look (window background, default
    text color, borders).

    The QLabel rule matters: QLabel is a QFrame subclass, so without it
    the QFrame rule gave EVERY label in all three apps a surface-colored
    box with a border (the stray rectangles around "GPUSA", "Admin User",
    page titles, card captions...). Labels that want a border or
    background set one in their own stylesheet, which still wins. Each app's own components style themselves
    further (see pos_app/gui/components and admin_app/gui/components)
    but should still pull their colors from the active palette dict
    rather than hardcoding hex values.
    """
    font_family = palette.get("font_family", FONT_FAMILY)
    radius_md = palette.get("radius_md", "0px")
    return f"""
        QWidget {{
            background-color: {palette['background']};
            color: {palette['text_primary']};
            font-family: {css_font_stack(font_family)};
        }}
        QFrame, QTableView, QTabWidget::pane {{
            background-color: {palette['surface']};
            border: 1px solid {palette['border']};
            border-radius: {radius_md};
        }}
        QLabel {{
            background-color: transparent;
            border: none;
        }}
    """


def apply_theme(
    app_or_widget,
    mode: str = "dark",
    palette: dict[str, str] | None = None,
) -> None:
    """Apply a palette to a QApplication or widget.

    This is the ONLY function that should call setStyleSheet with base
    palette colors. With no `palette` argument this is unchanged from
    before: `mode` picks DARK_PALETTE or LIGHT_PALETTE, the one shared
    look depot_app still uses.

    Passing `palette` (a dict with at least DARK_PALETTE's keys) lets a
    single app substitute its own visual identity - e.g. admin_app's
    Classical dark-serif palette or pos_app's Organic warm palette (see
    admin_app/theme.py, pos_app/theme.py) - without this file needing to
    know those palettes exist. `mode` is ignored when `palette` is given;
    the caller's dict is used exactly as passed. Two optional keys beyond
    DARK_PALETTE's set are recognized here: "font_family" (falls back to
    shared.constants.FONT_FAMILY) and "radius_md" (falls back to "0px",
    the original square-cornered look).
    """
    active_palette = palette if palette is not None else (DARK_PALETTE if mode == "dark" else LIGHT_PALETTE)
    app_or_widget.setStyleSheet(_build_qss(active_palette))
