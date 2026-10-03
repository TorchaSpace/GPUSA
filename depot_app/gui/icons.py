"""Verbatim SVG path data for depot_app's icons - copied from the mockup
source (Warehouse Console.dc.html / Warehouse Floor App.dc.html) rather
than redrawn, same convention as admin_app/gui/icons.py and
shared/gui_kit/icon_kit.py's docstring: each constant is the inner
markup of a `stroke="currentColor"` Feather-style icon, fed through
shared.gui_kit.icon_kit.svg_to_icon()/svg_to_pixmap() to bake in a
concrete color.

Only 5 distinct icons appear across both mockup pages - the full set.
"""

from __future__ import annotations

# Warehouse-crate brand mark - both pages' header logo.
CRATE_LOGO = (
    '<path d="M22 8.35V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8.35A2 2 0 0 1 3.26 6.5l8-3.2a2 2 0 0 1 '
    '1.48 0l8 3.2A2 2 0 0 1 22 8.35Z"></path><path d="M6 18h12"></path><path d="M6 14h12"></path>'
    '<rect x="6" y="10" width="12" height="12"></rect>'
)

# "İdari Giriş" (Admin Login) button, "Lock & exit" button.
LOCK = '<rect width="18" height="11" x="3" y="11" rx="0"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path>'

# Manager Portal modal's "secure session" top strip.
SHIELD = (
    '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 '
    '4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path>'
)

# "PO sent" / success confirmation banners.
CHECKMARK = '<path d="M20 6 9 17l-5-5"></path>'

# Low-stock alert banner; out-of-range price warning.
TRIANGLE_ALERT = (
    '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path>'
    '<path d="M12 9v4"></path><path d="M12 17h.01"></path>'
)
