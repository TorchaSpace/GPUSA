"""POS app's own visual identity: "Organic" - a warm, rounded, cream-toned
look taken from the POS.zip Claude Design mockup (minimalist-retail-pos-
design bundle, design system "organic-257b49c7-...").

Intentionally a SEPARATE, unrelated palette from admin_app's Classical
theme and depot_app's (future) Industry theme - see admin_app/theme.py's
docstring for why that divergence lives per-app rather than in
shared/theme.py. Pass ORGANIC_PALETTE to shared.theme.apply_theme(app,
palette=...) - see pos_app/main.py.

Values below are copied verbatim from the mockup's own source:
--color-bg/--color-surface/--color-text/--color-accent/--color-accent-2/
--font-heading/--font-body/--radius-sm-md-lg in
_ds/organic-.../styles.css, plus the RED/YEL hex literals the mockup's
own Component code (POS Dealership.dc.html) uses for out-of-stock/
low-stock badges - that design system defines no separate semantic
danger/warning/success tokens, so those two ad-hoc constants are the
mockup's actual "alert" colors, not an invention of mine. "border" below
approximates the mockup's --color-divider, which is a runtime
color-mix() (16% text over bg) that QSS can't express directly - the
closest static step in the same neutral scale (--color-neutral-300) is
used instead.

KNOWN GAP - webfonts: same constraint as admin_app/theme.py. The mockup
specifies "Caprasimo" (headings, a rounded display face) and "Figtree"
(body, a humanist sans) from Google Fonts; this sandbox's network egress
blocks fonts.googleapis.com (verified via curl - 403, "organization
policy"), and neither font ships as a file inside the bundle. FONT_HEADING/
FONT_BODY list the real font first, then a rounded/humanist-sans fallback
chain, so supplying the real font files later needs no code change; until
then Windows renders the fallback (Segoe UI, a humanist sans reasonably
close to Figtree; Caprasimo's bubbly display style has no close system
fallback, so headings will look plainer than the mockup until the real
font is available).
"""

from __future__ import annotations

# Headings use Caprasimo (a rounded display face) in the mockup; no
# common system font matches its bubbly style closely, so this falls
# back to a plain, legible sans rather than faking a "rounded" look.
FONT_HEADING = "Caprasimo, 'Segoe UI', system-ui, sans-serif"

# Body text uses Figtree (a humanist sans) in the mockup; Segoe UI is the
# closest commonly-available match on Windows.
FONT_BODY = "Figtree, 'Segoe UI', system-ui, sans-serif"

ORGANIC_PALETTE = {
    "background": "#f5ead8",
    "surface": "#ebddc5",
    "surface_raised": "#f9f4ed",  # --color-neutral-100
    "border": "#dcd3c4",  # --color-neutral-300 (nearest static step to --color-divider)
    "text_primary": "#201e1d",
    "text_secondary": "#645c50",  # --color-neutral-700
    "alert_critical": "#d8412f",  # mockup's own RED constant (out-of-stock badge)
    "alert_warning": "#f2c230",  # mockup's own YEL constant (low-stock badge)
    "alert_success": "#7a8a5e",  # --color-accent-2, used for in-stock state
    "accent": "#c67139",  # --color-accent
    "accent_2": "#7a8a5e",  # --color-accent-2
    "font_family": FONT_BODY,
    "font_heading": FONT_HEADING,
    "radius_sm": "8px",
    "radius_md": "16px",
    "radius_lg": "28px",
}
