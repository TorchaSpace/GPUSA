"""Persistent header shared by all 4 POS screens - recreates the
mockup's <header>: a brand avatar+name+location on the left, a pill-
shaped nav row in the middle, and a cashier name/clock/avatar on the
right.

Dropped from the mockup: the delivery-alert bell button - that whole
feature (simulated delivery orders, the alert overlay) belongs to a
Dealership/Orders domain that doesn't exist yet, and task #60 already
defers it explicitly. No dead button is shown for it.

The cashier on the right is whoever signed in at this till (see
pos_app/gui/auth_flow.py); the small "Switch" link under the clock signs
them out and asks the next cashier to sign in (`switch_cashier`).
"""

from __future__ import annotations

from PySide6.QtCore import QTime, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pos_app.gui.components.organic import PillNav
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.gui_kit.language_switch import LanguageSwitch
from shared.i18n import tr
from shared.textcase import upper

# (page key, string key): labels are looked up when the header is built, not at import.
NAV_ITEMS = [("home", "pos.nav.home"), ("sale", "pos.nav.sale"), ("receive", "pos.nav.receive"), ("stock", "pos.nav.stock"), ("sales", "pos.nav.sales")]

HEADER_HEIGHT_PX = 76


class PosHeader(QWidget):
    page_selected = Signal(str)
    switch_cashier = Signal()

    def __init__(
        self,
        dealership_name: str,
        location_line: str,
        cashier_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.setFixedHeight(HEADER_HEIGHT_PX)
        self.setObjectName("posHeader")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#posHeader {{ background-color: {p['background']}; }}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 0, 28, 0)
        layout.setSpacing(24)

        layout.addWidget(self._build_brand(dealership_name, location_line))
        layout.addWidget(self._build_nav(), stretch=1)
        layout.addStretch(0)
        layout.addWidget(LanguageSwitch(p, rounded=True))
        layout.addWidget(self._build_cashier(cashier_name))

    def _build_brand(self, dealership_name: str, location_line: str) -> QWidget:
        p = ORGANIC_PALETTE
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        avatar = QLabel(upper(dealership_name[:1]) if dealership_name else "?")
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background-color: {p['accent']}; color: white; border-radius: 20px; "
            f"font-family: {FONT_HEADING_CSS}; font-size: 20px;"
        )
        row.addWidget(avatar)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_label = QLabel(dealership_name)
        name_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 20px; color: {p['text_primary']};")
        location_label = QLabel(location_line)
        location_label.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        names.addWidget(name_label)
        names.addWidget(location_label)
        names_widget = QWidget()
        names_widget.setLayout(names)
        row.addWidget(names_widget)
        return container

    def _build_nav(self) -> QWidget:
        """The design's pill nav: ONE raised highlight slides between tabs."""
        self._nav = PillNav([(key, tr(label_key)) for key, label_key in NAV_ITEMS])
        self._nav.page_selected.connect(self.page_selected.emit)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self._nav)
        row.addStretch(1)
        return holder

    def _build_cashier(self, cashier_name: str) -> QWidget:
        p = ORGANIC_PALETTE
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_label = QLabel(cashier_name)
        self._cashier_label = name_label
        name_label.setAlignment(Qt.AlignRight)
        name_label.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {p['text_primary']};")
        self._clock_label = QLabel(QTime.currentTime().toString("HH:mm"))
        self._clock_label.setAlignment(Qt.AlignRight)
        self._clock_label.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        names.addWidget(name_label)
        clock_row = QHBoxLayout()
        clock_row.setSpacing(8)
        clock_row.addStretch(1)
        self.switch_button = QPushButton(tr("pos.header.switch"))
        self.switch_button.setCursor(Qt.PointingHandCursor)
        self.switch_button.setFlat(True)
        self.switch_button.setToolTip(tr("pos.header.switch_tip"))
        self.switch_button.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; color: {p['accent']}; font-size: 13px; "
            f"font-weight: 600; padding: 0; }}"
        )
        self.switch_button.clicked.connect(self.switch_cashier.emit)
        clock_row.addWidget(self.switch_button)
        clock_row.addWidget(self._clock_label)
        names.addLayout(clock_row)
        names_widget = QWidget()
        names_widget.setLayout(names)
        row.addWidget(names_widget)

        initials = upper("".join(part[0] for part in cashier_name.split()[:2])) or "?"
        avatar = QLabel(initials)
        self._avatar_label = avatar
        avatar.setFixedSize(44, 44)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background-color: {p['accent_2']}; color: #272e1b; border-radius: 22px; font-weight: 700;"
        )
        row.addWidget(avatar)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(30_000)
        return container

    def set_cashier(self, cashier_name: str) -> None:
        self._cashier_label.setText(cashier_name)
        self._avatar_label.setText(upper("".join(part[0] for part in cashier_name.split()[:2])) or "?")

    def _tick_clock(self) -> None:
        self._clock_label.setText(QTime.currentTime().toString("HH:mm"))

    def set_active(self, key: str) -> None:
        self._nav.set_active(key)
