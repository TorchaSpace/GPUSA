"""Manager Console: a SEPARATE, secondary top-level window opened from
the Floor kiosk's "İdari Giriş" button (see main_window.py) - never the
app's primary window. Recreates the "Warehouse Console" mockup's
back-office/manager view: a left sidebar (Dashboard/Warehouses/
Inventory/Shipments/Reports - all real now: dashboard_page.py,
inventory_page.py, shipments_page.py, reports_page.py; the mockup lists
those four but only drew Warehouses) and, within
Warehouses, a warehouse-card row plus Movement Logs / Workforce
Attendance tabs.

JUDGMENT CALL worth flagging: Erol's instruction "do not use a sidebar
navigation for this app" was given specifically about depot_app's
TOP-LEVEL shape (the Floor-kiosk-as-default-window point) - it's what
keeps main_window.py sidebar-free. Console is a secondary, admin-only
popout that exists specifically to reproduce the Manager Console mockup
"exactly as it appears" (Erol's other explicit instruction), and that
mockup's own layout IS a sidebar - so this window keeps it. Flagged in
the chat reply alongside this slice, in case that reading is wrong.

Real content: one card per active warehouse (warehouse_repository +
stock_repository - units held vs. capacity, status at the 85% threshold,
SKUs held, docks, today's units in/out; see components/warehouse_card.py),
with this depot's own warehouse marked and selected first. Clicking a
card shows that warehouse's stock movements in the "Movement Logs" tab
(Floor receipts/dispatches, shipment loading, transfers, counts). The
tab is READ-ONLY here: logging happens on the Floor kiosk; Console is the
oversight view of the same feed. The mockup's pallet counts and dock
occupancy aren't tracked, so they're not shown.

The "Workforce Attendance" tab is ALSO real now (see attendance_panel.py) -
unlike admin_app's Workforce page, this mockup tab (badge check-in/out,
on-floor count, today's roster) has no fabricated numbers at all, so it
was built field-for-field rather than left as a placeholder.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import depot_app.gui.icons as icons
from database import account_repository, stock_repository, warehouse_repository
from database.exceptions import DataAccessError
from depot_app.gui.attendance_panel import AttendancePanel
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.dashboard_page import DashboardPage
from depot_app.gui.inventory_page import InventoryPage
from depot_app.gui.reports_page import ReportsPage
from depot_app.gui.auth_flow import portal_unlock_dialog
from depot_app.gui.components.warehouse_card import WarehouseCard
from depot_app.gui.shipments_page import ShipmentsPage
from depot_app.gui.manager_portal_dialog import ManagerPortalDialog
from depot_app.theme import FONT_HEADING, INDUSTRY_PALETTE
from shared.formatting import local_time_text
from shared.gui_kit.icon_kit import svg_to_icon
from shared import current_session
from shared.auth import Session
from shared.models import UNASSIGNED, Warehouse
from shared.warehousing import direction_label, reason_label, reference_text, start_of_today_db

SIDEBAR_WIDTH_PX = 216

_NAV_ITEMS = ["Dashboard", "Warehouses", "Inventory", "Shipments", "Reports"]



class ConsoleWindow(QMainWindow):
    def __init__(self, warehouse: Warehouse, session: Session | None = None):
        super().__init__()
        self.warehouse = warehouse  # the warehouse this depot instance runs
        # The depot manager / administrator who signed in at the Floor's
        # "İdari Giriş" (depot_app/gui/auth_flow.py). Closing the Console
        # or "Sign out" ends it.
        self.session = session
        self._selected_code = warehouse.code
        self._cards: list[WarehouseCard] = []
        p = INDUSTRY_PALETTE
        self.setWindowTitle("Dockline Console")
        self.resize(1280, 820)
        self.setStyleSheet(f"QMainWindow {{ background-color: {p['background']}; }}")

        self._portal_dialog: ManagerPortalDialog | None = None

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        main_col = QVBoxLayout()
        main_col.setContentsMargins(24, 16, 24, 20)
        main_col.setSpacing(16)
        main_col.addWidget(self._build_header())

        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        for key in _NAV_ITEMS:
            if key == "Warehouses":
                page = self._build_warehouses_page()
            elif key == "Shipments":
                page = ShipmentsPage(warehouse.site_label, warehouse.code)
            else:
                page = {"Dashboard": DashboardPage, "Inventory": InventoryPage, "Reports": ReportsPage}[key](warehouse)
            self._pages[key] = page
            self._stack.addWidget(page)
        main_col.addWidget(self._stack, stretch=1)

        main_widget = QWidget()
        main_widget.setLayout(main_col)
        layout.addWidget(main_widget, stretch=1)

        self.setCentralWidget(central)
        self._navigate("Warehouses")

    def _build_sidebar(self) -> QWidget:
        p = INDUSTRY_PALETTE
        sidebar = QFrame()
        sidebar.setFixedWidth(SIDEBAR_WIDTH_PX)
        sidebar.setStyleSheet(f"background-color: {p['surface']}; border-right: 1px solid {p['border']};")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QWidget()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(18, 20, 18, 20)
        brand_layout.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(svg_to_icon(icons.CRATE_LOGO, p["accent"], size=22).pixmap(22, 22))
        brand_layout.addWidget(logo)
        wordmark = QLabel("DOCKLINE")
        wordmark.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 1.5px; "
            f"font-size: 17px; color: {p['text_primary']};"
        )
        brand_layout.addWidget(wordmark)
        brand_layout.addStretch(1)
        layout.addWidget(brand)

        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(8, 0, 8, 0)
        nav_layout.setSpacing(1)
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key in _NAV_ITEMS:
            button = QPushButton(key)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFlat(True)
            button.clicked.connect(lambda checked, k=key: self._navigate(k))
            button.setStyleSheet(
                f"""
                QPushButton {{
                    text-align: left; padding: 9px 10px; border: none; border-radius: 0;
                    color: {p['text_secondary']}; background: transparent;
                    font-family: '{p['font_family']}'; font-size: 13px;
                }}
                QPushButton:checked {{
                    color: {p['accent_900']}; background-color: {p['accent_100']};
                    border-left: 2px solid {p['accent']}; padding-left: 8px;
                }}
                """
            )
            nav_layout.addWidget(button)
            self._button_group.addButton(button)
            self._nav_buttons[key] = button
        nav_layout.addStretch(1)
        nav_widget = QWidget()
        nav_widget.setLayout(nav_layout)
        layout.addWidget(nav_widget, stretch=1)

        footer = QFrame()
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        footer.setObjectName("consoleFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        footer.setStyleSheet(f"#consoleFooter {{ border-top: 1px solid {p['border']}; }}")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(18, 12, 18, 12)
        footer_label = QLabel("Manager console")
        footer_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        footer_layout.addWidget(footer_label)
        layout.addWidget(footer)

        return sidebar

    def _navigate(self, key: str) -> None:
        self._stack.setCurrentWidget(self._pages[key])
        self._nav_buttons[key].setChecked(True)
        # The header used to say WAREHOUSES whichever page was open.
        if hasattr(self, "_title_label"):
            self._breadcrumb_label.setText(f"OPERATIONS / {key.upper()}")
            self._title_label.setText(key.upper())
        if key == "Warehouses":
            self._refresh_warehouses_page()

    def _build_header(self) -> QWidget:
        p = INDUSTRY_PALETTE
        header = QHBoxLayout()

        titles = QVBoxLayout()
        titles.setSpacing(2)
        breadcrumb = QLabel("OPERATIONS / WAREHOUSES")
        breadcrumb.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        titles.addWidget(breadcrumb)
        self._breadcrumb_label = breadcrumb
        title = QLabel("WAREHOUSES")
        self._title_label = title
        title.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 1px; "
            f"font-size: 30px; color: {p['text_primary']};"
        )
        titles.addWidget(title)
        titles_widget = QWidget()
        titles_widget.setLayout(titles)
        header.addWidget(titles_widget, stretch=1)

        self._clock_label = QLabel()
        self._clock_label.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 15px; color: {p['text_primary']};")
        header.addWidget(self._clock_label)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

        who = QVBoxLayout()
        who.setSpacing(0)
        self._who_label = QLabel()
        self._who_label.setAlignment(Qt.AlignRight)
        self._who_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {p['text_primary']};")
        self._who_role_label = QLabel()
        self._who_role_label.setAlignment(Qt.AlignRight)
        self._who_role_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        who.addWidget(self._who_label)
        who.addWidget(self._who_role_label)
        who_widget = QWidget()
        who_widget.setLayout(who)
        header.addWidget(who_widget)
        sign_out = IndustryButton("Sign out", variant="ghost")
        sign_out.clicked.connect(self.sign_out)
        header.addWidget(sign_out)

        admin_login = IndustryButton("İdari Giriş", variant="primary")
        admin_login.setIcon(svg_to_icon(icons.LOCK, "#ffffff", size=14))
        admin_login.clicked.connect(self._open_manager_portal)
        header.addWidget(admin_login)
        self._update_who()

        widget = QWidget()
        widget.setLayout(header)
        return widget

    def _tick_clock(self) -> None:
        from datetime import datetime

        self._clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    def _update_who(self) -> None:
        s = self.session
        self._who_label.setText(s.name if s else "Not signed in")
        self._who_role_label.setText(f"{s.role_label} · {s.badge_id}" if s else "")

    def set_session(self, session: Session) -> None:
        """Someone (re)signed in at the Floor's İdari Giriş."""
        self.session = session
        current_session.set(session)
        self._update_who()

    def sign_out(self) -> None:
        """End the manager session: lock the Portal, close the Console. The
        next İdari Giriş on the Floor asks for a sign-in again."""
        if self._portal_dialog is not None:
            self._portal_dialog.close()
        if self.session is not None:
            try:
                account_repository.sign_out(self.session)
            except DataAccessError:
                pass
        self.session = None
        current_session.clear()
        self._update_who()
        self.hide()

    def closeEvent(self, event) -> None:
        # Closing the Console window is signing out - it shouldn't leave a
        # manager session behind on a shared depot PC.
        self.sign_out()
        super().closeEvent(event)

    def _open_manager_portal(self, confirmed: bool = False) -> None:
        """The Portal (purchasing, treasury) asks the signed-in person's PIN
        again every time it's opened (`confirmed` skips that - tests)."""
        if self.session is None:
            return
        if not confirmed:
            dialog = portal_unlock_dialog(self.session, self)
            if not dialog.exec():
                return
        if self._portal_dialog is None:
            self._portal_dialog = ManagerPortalDialog(self.warehouse.site_label, self)
        self._portal_dialog.show()
        self._portal_dialog.raise_()
        self._portal_dialog.activateWindow()

    def _build_warehouses_page(self) -> QWidget:
        p = INDUSTRY_PALETTE
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._cards_row = QHBoxLayout()
        self._cards_row.setSpacing(12)
        cards_widget = QWidget()
        cards_widget.setLayout(self._cards_row)
        layout.addWidget(cards_widget)

        self._unassigned_note = QLabel()
        self._unassigned_note.setWordWrap(True)
        self._unassigned_note.setStyleSheet(
            f"font-size: 12px; color: {p['text_primary']}; background-color: #fff6d6; "
            f"border: 1px solid #f4b400; padding: 6px 10px;"
        )
        self._unassigned_note.hide()
        layout.addWidget(self._unassigned_note)

        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"""
            QTabWidget::pane {{ border: 1px solid {p['border']}; background-color: {p['surface']}; }}
            QTabBar::tab {{
                background: {p['background']}; color: {p['text_secondary']};
                border: 1px solid {p['border']}; padding: 8px 16px;
                font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 0.5px; font-size: 12px;
            }}
            QTabBar::tab:selected {{ color: {p['accent_900']}; background: {p['accent_100']}; }}
            """
        )
        moves_tab = QWidget()
        moves_layout = QVBoxLayout(moves_tab)
        moves_layout.setContentsMargins(12, 12, 12, 12)
        self._moves_note = QLabel()
        self._moves_note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        moves_layout.addWidget(self._moves_note)
        self._movements_table = self._build_movements_table()
        moves_layout.addWidget(self._movements_table, stretch=1)
        tabs.addTab(moves_tab, "Movement Logs")

        attendance_tab = QWidget()
        attendance_layout = QVBoxLayout(attendance_tab)
        attendance_layout.setContentsMargins(12, 12, 12, 12)
        self._attendance_panel = AttendancePanel()
        attendance_layout.addWidget(self._attendance_panel)
        tabs.addTab(attendance_tab, "Workforce Attendance")

        layout.addWidget(tabs, stretch=1)
        return page

    def _build_movements_table(self) -> QTableWidget:
        p = INDUSTRY_PALETTE
        table = QTableWidget(0, 8)
        table.setHorizontalHeaderLabels(["Time", "Movement", "SKU", "Product", "Qty", "Why", "Reference", "Handled by"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        table.setStyleSheet(
            f"""
            QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; gridline-color: {p['border']}; }}
            QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
                border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
            """
        )
        return table

    def _select_warehouse(self, code: str) -> None:
        self._selected_code = code
        for card in self._cards:
            card.set_selected(card.warehouse.code == code)
        self._refresh_movements()

    def _refresh_warehouses_page(self) -> None:
        self._attendance_panel.reload()
        try:
            warehouses = warehouse_repository.list_all(active_only=True)
            used = stock_repository.units_by_location()
            today = stock_repository.movement_totals_since(start_of_today_db())
            skus = {w.code: len(stock_repository.levels_at(w.location)) for w in warehouses}
        except DataAccessError:
            warehouses, used, today, skus = [], {}, {}, {}
        if not any(w.code == self.warehouse.code for w in warehouses):
            warehouses.insert(0, self.warehouse)  # e.g. deactivated in Admin - still this depot
        warehouses.sort(key=lambda w: (w.code != self.warehouse.code, w.code))

        for card in self._cards:
            self._cards_row.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards = []
        for w in warehouses:
            card = WarehouseCard(w, is_this_depot=w.code == self.warehouse.code)
            inbound, outbound = today.get(w.location, (0, 0))
            card.set_numbers(used.get(w.location, 0), skus.get(w.code, 0), inbound, outbound)
            card.clicked.connect(self._select_warehouse)
            self._cards_row.addWidget(card, stretch=1)
            self._cards.append(card)
        if not any(c.warehouse.code == self._selected_code for c in self._cards):
            self._selected_code = self.warehouse.code

        unassigned = used.get(UNASSIGNED, 0)
        self._unassigned_note.setVisible(bool(unassigned))
        self._unassigned_note.setText(
            f"{unassigned:,} units company-wide aren't placed at any warehouse or dealership yet "
            f"(stock from before warehouses were tracked). Place them in Admin > Warehouses."
        )
        self._select_warehouse(self._selected_code)

    def _refresh_movements(self) -> None:
        card = next((c for c in self._cards if c.warehouse.code == self._selected_code), None)
        warehouse = card.warehouse if card else self.warehouse
        self._moves_note.setText(f"Showing {warehouse.site_label} · click a card to switch warehouse")
        try:
            movements = stock_repository.list_movements(limit=100, location=warehouse.location)
        except DataAccessError:
            movements = []
        self._movements_table.setRowCount(len(movements))
        for row, movement in enumerate(movements):
            sign = "+" if movement["movement_type"] == "receive" else "−"
            values = [
                local_time_text(movement["created_at"]),
                direction_label(movement),
                movement["barcode"],
                movement["product_name"],
                f"{sign}{movement['quantity']}",
                reason_label(movement),
                reference_text(movement),
                movement.get("handled_by") or "—",
            ]
            for column, value in enumerate(values):
                self._movements_table.setItem(row, column, QTableWidgetItem(value))
