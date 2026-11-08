"""Staff: this dealership's people (shared/gui_kit/staff_view.py in the
till's colours). The shop adds its own staff with their national ID and
assigns them here, checks them in and out, and sees who came and who left
on any day. Admin only watches the same data.

A till that is not set up for a dealership (a dev run) has no staff to
manage and says so instead.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.auth import AREA_POS
from shared.gui_kit.staff_view import StaffView
from shared.i18n import tr
from shared.staff import DEALERSHIP, StaffPlace


class StaffPage(QWidget):
    def __init__(self, dealership_code: str | None, dealership_name: str | None, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.setObjectName("staffPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#staffPage {{ background-color: {p['background']}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 20)
        outer.setSpacing(12)

        place = StaffPlace(DEALERSHIP, dealership_code, dealership_name or "") if dealership_code else None
        subtitle = QLabel(tr("staff.page.subtitle_place").format(place=place.name or place.code) if place else "")
        subtitle.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        title = QLabel(tr("staff.page.title"))
        title.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 40px; color: {p['text_primary']};")
        outer.addWidget(subtitle)
        outer.addWidget(title)

        self.view: StaffView | None = None
        if place is None:
            note = QLabel(tr("staff.page.no_place"))
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size: 16px; color: {p['text_secondary']};")
            outer.addWidget(note)
            outer.addStretch(1)
            return
        self.view = StaffView(p, place=place, can_manage=True, area=AREA_POS)
        outer.addWidget(self.view, stretch=1)

    def reload(self) -> None:
        if self.view is not None:
            self.view.reload()
