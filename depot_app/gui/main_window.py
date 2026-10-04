"""Depot's primary window: the distraction-free Floor kiosk - per Erol's
explicit navigation decision, this (not a sidebar/multi-page shell like
admin_app's) is depot_app's top-level shape. Recreates the "Warehouse
Floor App" mockup: header, the real Low Stock Alert banner, a Check-in
Log aside, and the two
real Inbound/Outbound movement panels.

The "İdari Giriş" (Admin Login) button does NOT open a Manager Portal
modal directly here (as it does in the original mockup) - by Erol's
explicit instruction it instead opens the full Manager Console as a
separate, secondary top-level window (see console_window.py). Console's
OWN "İdari Giriş" button is what opens the Manager Portal
purchasing/treasury gate, matching the mockup's actual Console screen.

Which warehouse this Floor IS comes from the setup wizard (a
Depot_<n>.warehouse.json sidecar - see shared/warehouse_bootstrap.py);
without one it works as the default warehouse. Everything here moves
THAT warehouse's stock: Inbound/Outbound log against it, the Low Stock
banner reads its levels. The header shows the current shift (A/B/C by the clock, shared/shifts.py).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QScrollArea,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

import depot_app.gui.icons as icons
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.checkin_panel import CheckInPanel
from depot_app.gui.low_stock_banner import LowStockBanner
from depot_app.gui.movement_panel import MovementPanel
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.gui_kit.icon_kit import svg_to_icon
from shared.gui_kit.motion import fade_in, toast
from depot_app.gui.auth_flow import console_sign_in_dialog
from shared.gui_kit.language_switch import LanguageSwitch
from shared import current_session
from shared.auth import Session
from shared.i18n import tr
from shared.models import Warehouse
from shared.shifts import shift_for
from shared.warehouse_bootstrap import resolve_this_depot_warehouse



class MainWindow(QMainWindow):
    def __init__(self, warehouse: Warehouse | None = None):
        super().__init__()
        p = INDUSTRY_PALETTE
        # The warehouse this depot instance runs - its site label is what
        # purchase orders, ledger documents and shipments are stamped with.
        self.warehouse = warehouse if warehouse is not None else resolve_this_depot_warehouse()
        self.setWindowTitle(tr("depot.floor.window_title").format(site=self.warehouse.site_label))
        self.resize(1360, 860)
        self.setStyleSheet(f"QMainWindow {{ background-color: {p['background']}; }}")

        self._console_window = None  # lazily created, kept alive here (see _open_console)

        central = QWidget()
        central.setObjectName("floorCentral")
        central.setAttribute(Qt.WA_StyledBackground, True)
        central.setStyleSheet(f"#floorCentral {{ background-color: {p['background']}; }}")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 16, 24, 20)
        outer.setSpacing(16)

        outer.addWidget(self._build_header())
        self._low_stock_banner = LowStockBanner(self.warehouse.location)
        outer.addWidget(self._low_stock_banner)

        body = QHBoxLayout()
        body.setSpacing(16)

        self._checkin_panel = CheckInPanel(self.warehouse)
        self._checkin_panel.setFixedWidth(330)
        body.addWidget(self._checkin_panel)

        panels = QHBoxLayout()
        panels.setSpacing(16)
        self._receive_panel = MovementPanel("receive", self.warehouse.location)
        self._dispatch_panel = MovementPanel("dispatch", self.warehouse.location)
        # Receiving/dispatching a SKU can move it across the critical-stock
        # threshold either way, so both panels refresh the shared banner.
        self._receive_panel.movement_logged.connect(self._low_stock_banner.reload)
        self._dispatch_panel.movement_logged.connect(self._low_stock_banner.reload)
        panels.addWidget(self._receive_panel, stretch=1)
        panels.addWidget(self._dispatch_panel, stretch=1)
        panels_widget = QWidget()
        panels_widget.setLayout(panels)
        body.addWidget(panels_widget, stretch=1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        outer.addWidget(body_widget, stretch=1)

        # Small screens scroll instead of squeezing the kiosk's big controls.
        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QScrollArea.NoFrame)
        scroller.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        # Width only: a fixed minimum HEIGHT overrides the layout's own minimum, which squeezed the
        # big input boxes into each other. Without it the scroller scrolls when the content needs more.
        central.setMinimumWidth(1100)
        scroller.setWidget(central)
        self.setCentralWidget(scroller)
        fade_in(scroller, 260)

    def _build_header(self) -> QWidget:
        p = INDUSTRY_PALETTE
        header = QFrame()
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        header.setObjectName("floorHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setStyleSheet(f"#floorHeader {{ border-bottom: 1px solid {p['border']}; }}")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(svg_to_icon(icons.CRATE_LOGO, p["accent"], size=26).pixmap(26, 26))
        layout.addWidget(logo)

        wordmark = QLabel(tr("depot.floor.wordmark"))
        wordmark.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; letter-spacing: 1.5px; "
            f"font-size: 26px; color: {p['text_primary']};"
        )
        layout.addWidget(wordmark)

        tag = QLabel(self.warehouse.site_label)
        tag.setStyleSheet(
            f"background-color: {p['accent_100']}; color: {p['accent_900']}; border: 1px solid {p['accent']}; "
            f"padding: 5px 12px; font-size: 13px; font-weight: 600;"
        )
        layout.addWidget(tag)

        layout.addStretch(1)

        layout.addWidget(LanguageSwitch(p))

        shift = self._shift_label = QLabel()
        shift.setStyleSheet(f"font-size: 12px; letter-spacing: 1.2px; color: {p['text_secondary']};")
        layout.addWidget(shift)

        self._clock_label = QLabel()
        self._clock_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; color: {p['text_primary']};"
        )
        layout.addWidget(self._clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

        admin_login = IndustryButton(tr("depot.floor.admin_login"), variant="primary", height=48, font_px=17)
        admin_login.setIcon(svg_to_icon(icons.LOCK, "#ffffff", size=14))
        admin_login.clicked.connect(lambda _checked=False: self._open_console())  # clicked sends a bool - never let it pose as the session
        layout.addWidget(admin_login)

        return header

    def changeEvent(self, event) -> None:
        # Back from another window (a barcode reader types into whatever has focus):
        # land in the Inbound SKU box unless the person is already typing somewhere.
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            focused = self.focusWidget()
            if not isinstance(focused, QLineEdit) and hasattr(self, "_receive_panel"):
                self._receive_panel.focus_scan()

    def _tick_clock(self) -> None:
        from datetime import datetime

        now = datetime.now()
        self._clock_label.setText(now.strftime("%H:%M:%S"))
        self._shift_label.setText(tr("depot.floor.shift").format(shift=shift_for(now)))

    def _open_console(self, session: Session | None = None) -> None:
        """İdari Giriş: a depot manager or administrator signs in (badge +
        PIN, depot_app/gui/auth_flow.py) and the Manager Console opens for
        them. While their Console is open, the button just brings it back.
        `session` skips the dialog (tests)."""
        # Imported lazily to avoid a module-load-time cycle (console_window
        # imports nothing from main_window, but keeping the import local
        # here mirrors "the popout is opened on demand, not built eagerly").
        from depot_app.gui.console_window import ConsoleWindow

        console = self._console_window
        if session is None and not (console is not None and console.session is not None and console.isVisible()):
            dialog = console_sign_in_dialog(self.warehouse, self)
            if not dialog.exec() or dialog.session is None:
                return
            session = dialog.session
        if console is None:
            console = self._console_window = ConsoleWindow(self.warehouse, session)
            console.locked.connect(lambda reason: toast(self, reason, 5000))
            current_session.set(session)
        elif session is not None:
            console.set_session(session)
        console.show()
        self._console_window.raise_()
        self._console_window.activateWindow()
