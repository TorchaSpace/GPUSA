"""Sidebar nav icon path data, copied verbatim from the mockup's own inline
SVGs (each page file's <aside> nav - identical across all 9 pages) so the
icon shapes are pixel-faithful. Feed these to
shared.gui_kit.icon_kit.svg_to_icon(path, color) to get a QIcon in
whatever color a given nav state needs (default/hover/active).
"""

from __future__ import annotations

OVERVIEW = (
    '<rect x="3" y="3" width="7" height="9"></rect>'
    '<rect x="14" y="3" width="7" height="5"></rect>'
    '<rect x="14" y="12" width="7" height="9"></rect>'
    '<rect x="3" y="16" width="7" height="5"></rect>'
)

INVENTORY = (
    '<path d="M21 8 12 3 3 8v8l9 5 9-5z"></path>'
    '<path d="m3 8 9 5 9-5"></path>'
    '<path d="M12 13v8"></path>'
)

WAREHOUSES = (
    '<path d="M3 21V8l9-5 9 5v13"></path>'
    '<path d="M7 21v-8h10v8"></path>'
    '<path d="M7 17h10"></path>'
)

DEALERSHIPS = (
    '<path d="M3 9h18l-2-5H5z"></path>'
    '<path d="M4 9v11h16V9"></path>'
    '<path d="M9 20v-6h6v6"></path>'
)

DISTRIBUTION = (
    '<path d="M14 18V6H2v12h2"></path>'
    '<path d="M14 9h4l4 4v5h-2"></path>'
    '<circle cx="7" cy="18" r="2"></circle>'
    '<circle cx="17" cy="18" r="2"></circle>'
    '<path d="M9 18h6"></path>'
)

PURCHASE_REQUESTS = (
    '<path d="M9 11l3 3 8-8"></path>'
    '<path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"></path>'
)

WORKFORCE = (
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>'
    '<circle cx="9" cy="7" r="4"></circle>'
    '<path d="M22 21v-2a4 4 0 0 0-3-3.9"></path>'
    '<path d="M16 3.1a4 4 0 0 1 0 7.8"></path>'
)

REPORTS = '<path d="M3 3v18h18"></path><path d="m7 15 4-4 3 3 5-6"></path>'

TREASURY = (
    '<path d="M3 21h18"></path>'
    '<path d="M3 10h18"></path>'
    '<path d="m12 3 9 7H3z"></path>'
    '<path d="M6 10v8M10 10v8M14 10v8M18 10v8"></path>'
)

SETTINGS = (
    '<circle cx="12" cy="12" r="3"></circle>'
    '<path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1'
    'M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path>'
)
