"""Top-level Admin window: sidebar nav (SidebarNav) + a QStackedWidget of
pages, recreating the mockup's persistent-left-rail layout (see
admin_Dashboard.zip's 9 .dc.html pages, all sharing the same <aside>).

Nav keys match the mockup's page files 1:1 (overview/inventory/warehouses/
dealerships/distribution/workforce/reports/treasury/settings), plus one
extra "purchase_requests" entry for the mockup's own dead-end nav link
(href="#" in every page - it doesn't point at a real page even in the
mockup) which we treat as a page instead of leaving unclickable.

Per the "visual shell first, backend incrementally" scope decision: only
Overview, Inventory, Dealerships, Distribution, Purchase requests,
Warehouses, Workforce, Treasury & Ledger are wired to a real repository (see
their pages/*.py), and Settings (sign-in accounts) - Reports is still a
themed PlaceholderPage.

The "Purchase requests" sidebar badge is the real number of orders
awaiting approval (it used to be the mockup's hardcoded "4"), kept live
by a PollingTimer on purchase_order_repository.count_pending() - a depot
submitting an out-of-range order shows up here within a few seconds.
Reports is the one placeholder with a working escape hatch: the app's
original Sales Reports tab (real transaction data, PDF/Excel export)
already exists and works, so its placeholder offers a button that opens
it in a popup rather than hiding a working feature behind an unbuilt
page.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from database import account_repository, purchase_order_repository
from database.exceptions import DataAccessError

import admin_app.gui.icons as icons
from admin_app.gui.auth_flow import admin_sign_in_dialog
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.sidebar_nav import NavItem, NavSection, SidebarNav
from admin_app.gui.pages.dealerships_page import DealershipsPage
from admin_app.gui.pages.distribution_page import DistributionPage
from admin_app.gui.pages.inventory_page import InventoryPage
from admin_app.gui.pages.overview_page import OverviewPage
from admin_app.gui.pages.placeholder_page import PlaceholderPage
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

# (title, note) for every page that's still a themed placeholder - see
# module docstring. Filled in with real pages one at a time as each
# domain gets a repository/schema of its own. Only reports is left -
# every other page is real now (see imports above).
_PLACEHOLDER_COPY: dict[str, tuple[str, str]] = {
    "reports": (
        "Reports",
        "This page is still a placeholder, but Sales Reports itself already works with real "
        "transaction data - use the button above to open it.",
    ),
}


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
        self._register_page("settings", SettingsPage())
        self._register_page("workforce", WorkforcePage())
        self._treasury_page = TreasuryPage()
        self._register_page("treasury", self._treasury_page)
        for key, (title, note) in _PLACEHOLDER_COPY.items():
            self._register_page(key, PlaceholderPage(title, note))
        self._add_legacy_reports_button()

        layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        self._sidebar.page_selected.connect(self._show_page)
        self._show_page("overview")

        self._pending_count: int | None = None
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
        self._sidebar.set_badge("purchase_requests", str(count) if count else None)
        self._overview_page.set_pending_count(count)

    def _add_legacy_reports_button(self) -> None:
        """Reports is still a placeholder (see module docstring), but the
        original Sales Reports tab - real transaction data, PDF/Excel
        export - already works, so don't hide it behind an unbuilt page.
        """
        reports_page = self._pages["reports"]
        button = CompactButton("Open Sales Reports")
        button.clicked.connect(self._open_legacy_reports)
        if hasattr(reports_page, "add_header_action"):
            reports_page.add_header_action(button)

    def _open_legacy_reports(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("admin.reports_tab"))
        dialog.resize(900, 600)
        dialog_layout = QVBoxLayout(dialog)
        dialog_layout.addWidget(SalesReportsTab())
        dialog.exec()
