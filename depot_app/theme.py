"""Depot app's own visual identity: "Industry" - a light, square-cornered
"blueprint/technical drafting" look taken from the Warehouse_son.zip
Claude Design mockup (two standalone apps - "Warehouse Console.dc.html"
and "Warehouse Floor App.dc.html" - sharing one design system,
"industry-4b6651bb-...").

Intentionally a SEPARATE, unrelated palette from admin_app's Classical
theme and pos_app's Organic theme - see admin_app/theme.py's docstring
for why that divergence lives per-app rather than in shared/theme.py.
Pass INDUSTRY_PALETTE to shared.theme.apply_theme(app, palette=...) - see
depot_app/main.py.

Values below are copied verbatim from the mockup's own source
(_ds/industry-.../styles.css `:root`, plus the two page files' own
overrides), not eyeballed from a screenshot:

    --color-bg:#f2f2f3  --color-surface:#e9e9ea  --color-text:#1d1f20
    --color-accent:#5980a6  (accent-2, #728fab, is defined in the DS but
    never actually used by either page - omitted here for the same
    reason)
    --font-heading:"Barlow Condensed" (uppercase + weight 600 throughout
    both pages)  --font-body:"Barlow"

THE ONE BIG OVERRIDE - border radius: the DS nominally defines
--radius-sm/md/lg as 2px/4px/7px, but a bottom-of-file rule in the same
stylesheet (`.card, .btn, .input, .tag, .seg, .dialog { border-radius: 0;
}`) collapses all of that to zero everywhere it matters, and both pages
lean into it further with a "blueprint" corner-tick-mark motif (four
small L-shaped registration marks drawn at each card/button/modal's
corners - see gui/components/blueprint_frame.py). Square corners +
corner ticks are this theme's actual, load-bearing identity, not an
afterthought - so radius_sm/md/lg are all "0px" here, not the DS's
nominal scale.

No semantic status-color tokens are defined in the DS. The two mockup
pages improvise, and inconsistently with each other (see the subagent
analysis this palette was built from): amber `#f4b400` for
warnings/alerts (low-stock banner, out-of-range price), the blue accent
ramp for positive/approved/in-range, and an inverted black-bg/off-white-
text treatment for critical/overdue/blocked. This file picks ONE
consistent choice for depot_app rather than reproducing the mockups'
inconsistency: amber for "needs attention" (alert_warning/alert_critical
share the same amber family here, since depot_app has no separate
"blocked" concept yet) and the accent blue for success/in-range.

KNOWN GAP - webfonts: same constraint as admin_app/theme.py and
pos_app/theme.py. The mockup specifies "Barlow Condensed" (headings) and
"Barlow" (body) loaded from Google Fonts. This sandbox's network egress
blocks fonts.googleapis.com (verified via curl for the other two
bundles' fonts - 403, "organization policy" - same CDN host, so this is
near-certain to be identical for Barlow), and neither font ships as a
file inside this bundle either. FONT_HEADING/FONT_BODY below list the
real font first, then a condensed/grotesk-sans fallback chain, so
supplying the real font files later needs no code change; until then
Windows renders the fallback (Bahnschrift/Arial Narrow approximate
Barlow Condensed's condensed-grotesk look reasonably well; Segoe UI
stands in for Barlow's plain grotesk body text).
"""

from __future__ import annotations

# Headings use Barlow Condensed (uppercase, weight 600) in the mockup;
# Bahnschrift and Arial Narrow are the closest condensed grotesk faces
# commonly present on Windows.
FONT_HEADING = "Barlow Condensed, Bahnschrift, 'Arial Narrow', sans-serif"

# Body text uses Barlow (a plain grotesk) in the mockup; Segoe UI is the
# closest commonly-available match on Windows.
FONT_BODY = "Barlow, 'Segoe UI', system-ui, sans-serif"

INDUSTRY_PALETTE = {
    "background": "#f2f2f3",
    "surface": "#e9e9ea",
    "surface_raised": "#f5f5f8",  # --color-neutral-100
    "border": "#d4d4d7",  # --color-neutral-300 (nearest static step to --color-divider)
    "text_primary": "#1d1f20",
    "text_secondary": "#5d5d60",  # --color-neutral-700
    "alert_critical": "#f4b400",  # mockup's own amber warning/hazard color (see docstring)
    "alert_warning": "#f4b400",
    "alert_success": "#597ea3",  # --color-accent-600 - the mockup's own stand-in for "positive"
    "accent": "#5980a6",
    "accent_100": "#eef6ff",
    "accent_900": "#1d2d3d",
    "font_family": FONT_BODY,
    "font_heading": FONT_HEADING,
    "radius_sm": "0px",
    "radius_md": "0px",
    "radius_lg": "0px",
}
