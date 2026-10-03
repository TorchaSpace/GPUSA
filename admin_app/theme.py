"""Admin app's own visual identity: "Classical" - a dark serif look taken
from the admin_Dashboard.zip Claude Design mockup (inventory-management-
dashboard bundle, design system "classical-05895d3f-...").

This is intentionally a SEPARATE, unrelated palette from pos_app's
Organic theme and depot_app's (future) Industry theme - the three apps
were each designed as their own product, not a shared design system, and
architecture.md's "only mechanics belong in shared/" rule means that
divergence lives here, in admin_app, not in shared/theme.py. Pass
CLASSICAL_PALETTE to shared.theme.apply_theme(app, palette=...) - see
admin_app/main.py.

Values below are verified against the mockup's actual source, not
eyeballed from a screenshot. Note the twist: the shared
_ds/classical-.../styles.css design-system file itself defines a LIGHT
palette (--color-bg:#f3f2f2) - but every one of the 9 page files
(Dealerships.dc.html, Inventory.dc.html, etc.) overrides that with its
own inline dark tokens (`color-scheme:dark; --color-bg:#161514;
--color-surface:#1f1d1c; --color-text:#eae7e7; --color-accent:#e1ad66;
--color-divider:color-mix(in srgb,#eae7e7 13%,transparent)`) on the root
wrapper div - so the dark look below is what every page actually renders,
confirmed by grepping the page files directly, not the design-system
default. Status colors (#86c49a success/green, #df9460 warning/orange,
#d9776c danger/red) are likewise hardcoded, identically, across every
page that needs them (Settings' "2FA Enabled", Treasury's "Overdue",
Workforce's absence count, Distribution's "Delayed shipments") - so
they're this app's real, consistent semantic palette, not a one-off.
"border"/"surface_raised" approximate the pages' own color-mix()
divider (computed: ~13% #eae7e7 over #161514/#1f1d1c), since QSS has no
color-mix() function.

KNOWN GAP - webfonts: the mockup specifies "Cormorant Garamond" (headings)
and "Lora" (body) loaded from Google Fonts. This sandbox's network egress
blocks fonts.googleapis.com (verified: a direct curl to it returns a 403
via the environment's proxy, "organization policy"), and neither font
ships as a file inside the mockup bundle - it's a CDN-only reference. So
neither font can be downloaded or bundled from here. FONT_HEADING/
FONT_BODY below list the real font first, followed by a serif system-font
fallback chain, so if Erol installs Cormorant Garamond/Lora locally (or
supplies the .ttf/.otf files later) the app will pick them up automatically
with no code change; until then Windows will render the fallback (Georgia
for headings, Constantia/Cambria for body) which is visually close (both
are serif text faces) but not pixel-identical to the mockup.
"""

from __future__ import annotations

# Headings use Cormorant Garamond in the mockup; Georgia is the closest
# serif commonly present on Windows as a fallback.
FONT_HEADING = "Cormorant Garamond, Georgia, 'Times New Roman', serif"

# Body text uses Lora in the mockup; Constantia/Cambria are the closest
# serif text faces commonly present on Windows.
FONT_BODY = "Lora, Constantia, Cambria, Georgia, serif"

CLASSICAL_PALETTE = {
    "background": "#161514",
    "surface": "#1f1d1c",
    "surface_raised": "#26231f",
    "border": "#393736",  # ~13% #eae7e7 over #1f1d1c, matching the mockup's --color-divider
    "text_primary": "#eae7e7",
    "text_secondary": "#a39d95",  # --color-neutral-400 range in the dark tokens
    "alert_critical": "#d9776c",
    "alert_success": "#86c49a",
    "alert_warning": "#df9460",
    "accent": "#e1ad66",
    "font_family": FONT_BODY,
    "font_heading": FONT_HEADING,
    "radius_sm": "2px",
    "radius_md": "4px",
    "radius_lg": "7px",
}
