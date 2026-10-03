"""Top-level Admin window: sidebar nav (SidebarNav) + a QStackedWidget of
pages, recreating the mockup's persistent-left-rail layout (see
admin_Dashboard.zip's 9 .dc.html pages, all sharing the same <aside>).

Nav keys match the mockup's page files 1:1 (overview/inventory/warehouses/
dealerships/distribution/workforce/reports/treasury/settings), plus one
extra "purchase_requests" entry for the mockup's own dead-end nav link
(href="#" in every page - it doesn't point at a real page even in the
mockup) which we treat as a page instead of leaving unclickable.

Every page is a real, repository-backed page now (see pages/*.py); the
themed PlaceholderPage is no longer used by any nav entry.

The "Purchase requests" sidebar badge is the real number of orders
awaiting approval (it used to be the mockup's hardcoded "4"), kept live
by a PollingTimer on purchase_order_repository.count_pending() - a depot
submitting an out-of-range order shows up here within a few seconds.
Reports also keeps a "Sales by product" button that opens the app's
original per-product Sales Reports tab in a popup.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from database import account_repository, purchase_order_repository, settings_repository
from database.exceptions import DataAccessError

import admin_app.gui.icons as icons
from admin_app.gui.auth_flow import admin_sign_in_dialog
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.global_search import GlobalSearchDialog
from admin_app.gui.components.sidebar_nav import NavItem, NavSection, SidebarNav
from admin_app.gui.pages.dealerships_page import DealershipsPage
from admin_app.gui.pages.distribution_page import DistributionPage
from admin_app.gui.pages.inventory_page import InventoryPage
from admin_app.gui.pages.overview_page import OverviewPage
from admin_app.gui.pages.reports_page import ReportsPage
from admin_app.gui.pages.settings_page import SettingsPage
from admin_app.gui.pages.warehouses_page import WarehousesPage
from admin_app.gui.pages.purchase_requests_page import PurchaseRequestsPage
from admin_app.gui.pages.treasury_page import TreasuryPage
from admin_app.gui.pages.workforce_page import WorkforcePage
from admin_app.gui.sales_reports_tab import SalesReportsTab
from shared import current_session
from shared.auth import Session
from shared.constants import PURCHASE_REQUEST_POLL_INTERVAL_MS
from shared.gui_kit.polling import PollingTimer
from shared.i18n import tr

NAV_SECTIONS = [
    NavSection(
        "Operations",
        [
            NavItem("overview", "Overview", icons.OVERVIEW),
            NavItem("inventory", "Inventory", icons.INVENTORY),
            NavItem("warehouses", "Warehouses", icons.WAREHOUSES),
            NavItem("dealerships", "Dealerships", icons.DEALERSHIPS),
            NavItem("distribution", "Distribution", icons.DISTRIBUTION),
            NavItem("purchase_requests", "Purchase requests", icons.PURCHASE_REQUESTS),  # badge: live, see module docstring
        ],
    ),
    NavSection(
        "People & records",
        [
            NavItem("workforce", "Workforce", icons.WORKFORCE),
            NavItem("reports", "Reports", icons.REPORTS),
            NavItem("treasury", "Treasury & Ledger", icons.TREASURY),
            NavItem("settings", "Settings", icons.SETTINGS),
        ],
    ),
]

class MainWindow(QMainWindow):
    def __init__(self, session: Session | None = None):
        super().__init__()
        self.setWindowTitle(tr("admin.window_title"))
        # Who signed in (admin_app/main.py). None only in tests / tools that
        # build the window directly.
        self.session = session
        current_session.set(session)
        self.resize(1400, 900)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = SidebarNav(
            NAV_SECTIONS,
            brand_title="GPUSA",
            brand_subtitle="Admin Dashboard",
            user_name=session.name if session else "Not signed in",
            user_role=session.role_label if session else "",
        )
        self._sidebar.sign_out_requested.connect(self.sign_out)
        layout.addWidget(self._sidebar)

        self._pending_count: int | None = None
        self._approval_buttons: list[CompactButton] = []
        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        self._overview_page = OverviewPage()
        self._overview_page.open_purchase_requests.connect(lambda: self.navigate("purchase_requests"))
        self._register_page("overview", self._overview_page)
        self._register_page("inventory", InventoryPage())
        self._register_page("dealerships", DealershipsPage())
        self._register_page("distribution", DistributionPage())
        self._purchase_requests_page = PurchaseRequestsPage()
        self._purchase_requests_page.pending_count_changed.connect(self._set_pending_count)
        self._register_page("purchase_requests", self._purchase_requests_page)
        self._register_page("warehouses", WarehousesPage())
        self._settings_page = SettingsPage()
        self._settings_page.notifications_changed.connect(self._refresh_pending_display)
        self._register_page("settings", self._settings_page)
        self._register_page("workforce", WorkforcePage())
        self._treasury_page = TreasuryPage()
        self._register_page("treasury", self._treasury_page)
        self._reports_page = ReportsPage()
        self._register_page("reports", self._reports_page)
        self._add_legacy_reports_button()
        self._install_shared_header_controls()

        layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        self._sidebar.page_selected.connect(self._show_page)
        self._show_page("overview")

        # Ctrl+K on Windows/Linux, Cmd+K on macOS (Qt maps "Ctrl" to Cmd there).
        self._search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._search_shortcut.activated.connect(self.open_search)
        self._pending_poller = PollingTimer(
            self._poll_pending_count, interval_ms=PURCHASE_REQUEST_POLL_INTERVAL_MS, parent=self
        )
        self._pending_poller.result_ready.connect(self._on_pending_polled)
        self._pending_poller.start()

    def _register_page(self, key: str, widget: QWidget) -> None:
        self._pages[key] = widget
        self._stack.addWidget(widget)

    def _show_page(self, key: str) -> None:
        widget = self._pages.get(key)
        if widget is not None:
            self._stack.setCurrentWidget(widget)
            if key == "purchase_requests":
                self._purchase_requests_page.reload()  # always fresh when opened
            elif key == "treasury":
                self._treasury_page.reload()  # "overdue" depends on today; depot may have recorded entries
            elif key == "reports":
                self._reports_page.reload()  # sales keep arriving from the tills

    def sign_out(self) -> None:
        """Sign out, hide everything, and ask for a sign-in again; Quit
        there closes Admin. Another administrator may sign in."""
        if self.session is not None:
            try:
                account_repository.sign_out(self.session)
            except DataAccessError:
                pass
        self.session = None
        current_session.clear()
        self.hide()
        dialog = admin_sign_in_dialog(cancel_text="Quit")
        if dialog.exec() == QDialog.Accepted and dialog.session is not None:
            self.session = dialog.session
            current_session.set(self.session)
            self._sidebar.set_user(self.session.name, self.session.role_label)
            self.show()
        else:
            QApplication.quit()

    def navigate(self, key: str) -> None:
        """Programmatic navigation (e.g. Overview's "Pending approvals"
        button) - keeps the sidebar highlight in sync."""
        self._sidebar.set_active(key)
        self._show_page(key)

    # --- live "Purchase requests" badge ----------------------------------

    @staticmethod
    def _poll_pending_count() -> int | None:
        # Broad on purpose: a transient "database is locked" on a timer
        # tick should just skip that tick.
        try:
            return purchase_order_repository.count_pending()
        except Exception:
            return None

    def _on_pending_polled(self, count: int | None) -> None:
        if count is None or count == self._pending_count:
            return
        self._set_pending_count(count)
        if self._stack.currentWidget() is self._purchase_requests_page:
            self._purchase_requests_page.reload()

    def _set_pending_count(self, count: int) -> None:
        self._pending_count = count
        self._refresh_pending_display()

    def _refresh_pending_display(self) -> None:
        """Badge and header counts follow the Notifications setting."""
        count = self._pending_count or 0
        shown = count if settings_repository.safe_notifications().pending_approvals else 0
        self._sidebar.set_badge("purchase_requests", str(shown) if shown else None)
        self._overview_page.set_pending_count(count)  # Overview's own card is data, not an alert
        for button in self._approval_buttons:
            self._style_approvals_button(button, shown)

    def _add_legacy_reports_button(self) -> None:
        """The original per-product Sales Reports tab (real transaction
        data, PDF/Excel export) still works and answers a different
        question - what sold, by product, over any date range - so the
        Reports page keeps a button that opens it."""
        button = CompactButton("Sales by product")
        button.setToolTip("Per-product sales for any date range")
        button.clicked.connect(self._open_legacy_reports)
        self._reports_page.add_header_action(button)

    def _open_legacy_reports(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("admin.reports_tab"))
        dialog.resize(900, 600)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(SalesReportsTab())
        dialog.exec()

    # --- header controls shared by every page ------------------------------

    def _install_shared_header_controls(self) -> None:
        """A "Search" button on every page and, except on Overview (which has
        its own) and Purchase requests (where you already are), a "Pending
        approvals" button that jumps to the queue."""
        for key, page in self._pages.items():
            if not hasattr(page, "add_leading_header_action"):
                continue
            search = CompactButton("Search  \u2318K" if sys.platform == "darwin" else "Search  Ctrl+K")
            search.setToolTip("Find a product, dealership, warehouse or person")
            search.clicked.connect(self.open_search)
            page.add_leading_header_action(search)
            if key in ("overview", "purchase_requests"):
                continue
            approvals = CompactButton("Pending approvals")
            approvals.clicked.connect(lambda _=False: self.navigate("purchase_requests"))
            self._style_approvals_button(approvals, self._pending_count or 0)
            page.add_leading_header_action(approvals)
            self._approval_buttons.append(approvals)

    @staticmethod
    def _style_approvals_button(button: CompactButton, count: int) -> None:
        button.setText(f"Pending approvals  {count}" if count else "Pending approvals")

    def open_search(self) -> None:
        dialog = GlobalSearchDialog(self)
        dialog.page_chosen.connect(self.navigate)
        dialog.exec()
