"""Turn a Feather-style inline SVG path fragment into a colored QIcon.

The three mockups all use the same convention for their line icons: a
16x16 viewBox="0 0 24 24" SVG with stroke="currentColor", so the same
path data renders in whatever color CSS gives it (default, hover,
active). Qt has no CSS "currentColor" equivalent, so this module bakes a
concrete color into the SVG text before rendering it, and gives each app
a small svg_to_icon() call instead of shipping actual .svg/.png asset
files (there are none in the mockup bundles - the icons are inline markup).

This is a MECHANISM (any app can call it with its own path data and
color), so it lives here per shared/gui_kit's own rule - the actual path
data for each icon is app-specific content and lives in each app's own
gui/icons.py.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
    'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke}" '
    'stroke-linecap="round" stroke-linejoin="round">{path}</svg>'
)


def svg_to_pixmap(path_markup: str, color: str, size: int = 16, stroke: float = 1.5) -> QPixmap:
    """Render a `<path>/<rect>/<circle>...` fragment (no wrapping <svg>) to a QPixmap.

    `path_markup` is exactly the inner markup a mockup's icon uses (e.g.
    '<circle cx="12" cy="12" r="3"></circle><path d="..."></path>') -
    copy it verbatim from the mockup rather than re-drawing the icon by
    hand, so the shape stays pixel-faithful.
    """
    svg_text = _SVG_TEMPLATE.format(size=size, color=color, path=path_markup, stroke=stroke)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    # Qt.transparent, not 0: PySide6 reads a bare 0 as Qt.color0, which
    # fills an ordinary QPixmap with OPAQUE BLACK - every icon used to
    # sit on a black square (invisible on admin's dark theme, very
    # visible on depot's and POS's light ones).
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def svg_to_icon(path_markup: str, color: str, size: int = 16, stroke: float = 1.5) -> QIcon:
    return QIcon(svg_to_pixmap(path_markup, color, size, stroke))
