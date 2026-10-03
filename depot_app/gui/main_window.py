"""Depot's primary window: the distraction-free Floor kiosk - per Erol's
explicit navigation decision, this (not a sidebar/multi-page shell like
admin_app's) is depot_app's top-level shape. Recreates the "Warehouse
Floor App" mockup: header, the real Low Stock Alert banner, a Check-in
Log aside (placeholder - no staff/badge backend exists), and the two
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
banner reads its levels. "Shift B" is still a placeholder (no shift or
session model exists).
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

import depot_app.gui.icons as icons
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.components.placeholder_panel import PlaceholderPanel
from depot_app.gui.low_stock_banner import LowStockBanner
from depot_app.gui.movement_panel import MovementPanel
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.gui_kit.icon_kit import svg_to_icon
from depot_app.gui.auth_flow import console_sign_in_dialog
from shared import current_session
from shared.auth import Session
from shared.models import Warehouse
from shared.warehouse_bootstrap import resolve_this_depot_warehouse

SHIFT_LABEL = "Shift B"


class MainWindow(QMainWindow):
    def __init__(self, warehouse: Warehouse | None = None):
        super().__init__()
        p = INDUSTRY_PALETTE
        # The warehouse this depot instance runs - its site label is what
        # purchase orders, ledger documents and shipments are stamped with.
        self.warehouse = warehouse if warehouse is not None else resolve_this_depot_warehouse()
        self.setWindowTitle(f"Dockline Floor · {self.warehouse.site_label}")
        self.resize(1360, 860)
        self.setStyleSheet(f"QMainWindow {{ background-color: {p['background']}; }}")

        self._console_window = None  # lazily created, kept alive here (see _open_console)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 16, 24, 20)
        outer.setSpacing(16)

        outer.addWidget(self._build_header())
        self._low_stock_banner = LowStockBanner(self.warehouse.location)
        outer.addWidget(self._low_stock_banner)

        body = QHBoxLayout()
        body.setSpacing(16)

        aside = PlaceholderPanel(
            "Check-in Log",
            "Badge check-in/out and the floor roster aren't modeled in the database yet - "
            "this panel will come alive once staff records exist.",
        )
        aside.setFixedWidth(260)
        body.addWidget(aside)

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

        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        p = INDUSTRY_PALETTE
        header = QFrame()
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        header.setObjectName("floorHeader")
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setStyleSheet(f"#floorHeader {{ border-bottom: 1px solid {p['border']}; }}")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 14)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(svg_to_icon(icons.CRATE_LOGO, p["accent"], size=26).pixmap(26, 26))
        layout.addWidget(logo)

        wordmark = QLabel("DOCKLINE FLOOR")
        wordmark.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; letter-spacing: 1.5px; "
            f"font-size: 20px; color: {p['text_primary']};"
        )
        layout.addWidget(wordmark)

        tag = QLabel(self.warehouse.site_label)
        tag.setStyleSheet(
            f"background-color: {p['accent_100']}; color: {p['accent_900']}; border: 1px solid {p['accent']}; "
            f"padding: 4px 10px; font-size: 12px; font-weight: 600;"
        )
        layout.addWidget(tag)

        layout.addStretch(1)

        shift = QLabel(SHIFT_LABEL)
        shift.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(shift)

        self._clock_label = QLabel()
        self._clock_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 16px; color: {p['text_primary']};"
        )
        layout.addWidget(self._clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

        admin_login = IndustryButton("İdari Giriş", variant="primary")
        admin_login.setIcon(svg_to_icon(icons.LOCK, "#ffffff", size=14))
        admin_login.clicked.connect(self._open_console)
        layout.addWidget(admin_login)

        return header

    def _tick_clock(self) -> None:
        from datetime import datetime

        self._clock_label.setText(datetime.now().strftime("%H:%M:%S"))

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
            current_session.set(session)
        elif session is not None:
            console.set_session(session)
        console.show()
        self._console_window.raise_()
        self._console_window.activateWindow()
