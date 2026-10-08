"""Line icons copied verbatim from the POS design mockup (Lucide paths,
24x24, drawn with a heavy 2.75 stroke). Render them with `icon(path, colour, size)`."""

from __future__ import annotations

from PySide6.QtGui import QIcon

from shared.gui_kit.icon_kit import svg_to_icon, svg_to_pixmap

STROKE = 2.75

CART = ('<circle cx="8" cy="21" r="1"></circle><circle cx="19" cy="21" r="1"></circle>'
        '<path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"></path>')
TRUCK = ('<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"></path><path d="M15 18H9"></path>'
         '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"></path>'
         '<circle cx="17" cy="18" r="2"></circle><circle cx="7" cy="18" r="2"></circle>')
PACKAGE = ('<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"></path>'
           '<path d="M12 22V12"></path><path d="m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7"></path><path d="m7.5 4.27 9 5.15"></path>')
SEARCH = '<circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path>'
CHECK = '<path d="M20 6 9 17l-5-5"></path>'
WARNING = ('<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"></path>'
           '<path d="M12 9v4"></path><path d="M12 17h.01"></path>')
BELL = ('<path d="M10.268 21a2 2 0 0 0 3.464 0"></path><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673'
        'C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"></path>')


def icon(path: str, colour: str, size: int = 24, stroke: float = STROKE) -> QIcon:
    return svg_to_icon(path, colour, size, stroke)


def pixmap(path: str, colour: str, size: int = 24, stroke: float = STROKE):
    return svg_to_pixmap(path, colour, size, stroke)
