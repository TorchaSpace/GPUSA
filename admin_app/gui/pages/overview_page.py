"""Overview page - recreates Inventory Dashboard.dc.html's layout: three
metric cards, then a "Product inventory" table with a below-reorder
filter, minus everything that needs a data domain that doesn't exist yet.

Real vs. placeholder, explicitly:
- Product inventory table: REAL, from database.product_repository - the
  same data source as the old ProductManagementTab/InventoryHealthTab.
- "Below reorder only" filter: REAL (client-side over the same query).
- Revenue MTD / Warehouses / Dealerships metric cards: PLACEHOLDER -
  there's no per-location stock, no dealership, and no revenue-by-channel
  data model yet. Cards say so via their corner note rather than
  pretending the number is live.
- "Pending approvals <n>" header button: REAL count of purchase orders
  awaiting approval (purchase_order_repository.count_pending(), kept live
  by MainWindow's poller). The mockup's button opens a dropdown of the
  held requests; here it opens the Purchase Requests page, which has the
  same approval queue - one place to approve from, not two copies.
- The mockup's per-location table columns and search-everything header
  bar are dropped entirely - they depend on a locations domain that
  doesn't exist yet, and a header control that does nothing is worse
  than one that isn't there. See architecture.md for the full scope note.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.inventory_table import InventoryTable
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.theme import CLASSICAL_PALETTE
from database import product_repository
from database.exceptions import DataAccessError
from shared.models import Product


class OverviewPage(AdminPage):
    open_purchase_requests = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Operations Overview", parent)

        self._approvals_button = CompactButton("Pending approvals")
        self._approvals_button.setToolTip("Purchase orders held for your approval")
        self._approvals_button.clicked.connect(self.open_purchase_requests.emit)
        self.add_header_action(self._approvals_button)

        refresh_button = CompactButton("Refresh")
        refresh_button.clicked.connect(self.reload)
        self.add_header_action(refresh_button)

        self._all_products: list[Product] = []
        self._low_only = False

        self.body_layout().addWidget(self._build_metric_row())

        self._table_section = Section("Stock overview", "Product inventory")
        self._low_only_checkbox = QCheckBox("Below reorder only")
        self._low_only_checkbox.setStyleSheet(f"color: {CLASSICAL_PALETTE['text_secondary']}; font-size: 12px;")
        self._low_only_checkbox.toggled.connect(self._on_low_only_toggled)
        self._table_section.add_header_control(self._low_only_checkbox)

        self._table = InventoryTable()
        self._table.setMinimumHeight(360)
        self._table_section.body_layout().addWidget(self._table)
        self.body_layout().addWidget(self._table_section, stretch=1)

        self.reload()

    def _build_metric_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        revenue_card = StatCard(
            "Total revenue · MTD", "—", corner_note="not tracked yet"
        )
        for label, value in (("Wholesale", "—"), ("Dealer", "—"), ("Direct", "—")):
            revenue_card.footer_layout().addWidget(stat_breakdown_item(label, value))
        layout.addWidget(revenue_card)

        warehouses_card = StatCard("Warehouses", "—", corner_note="not tracked yet")
        note = stat_breakdown_item("Capacity", "no warehouse data yet")
        warehouses_card.footer_layout().addWidget(note)
        layout.addWidget(warehouses_card)

        dealerships_card = StatCard("Dealerships", "—", corner_note="not tracked yet")
        for label, value in (("Orders today", "—"), ("Avg. fill", "—"), ("In transit", "—")):
            dealerships_card.footer_layout().addWidget(stat_breakdown_item(label, value))
        layout.addWidget(dealerships_card)

        return row

    def set_pending_count(self, count: int) -> None:
        self._approvals_button.setText(f"Pending approvals  {count}" if count else "Pending approvals")

    def _on_low_only_toggled(self, checked: bool) -> None:
        self._low_only = checked
        self._render_table()

    def reload(self) -> None:
        try:
            self._all_products = product_repository.list_all()
        except DataAccessError:
            self._all_products = []
        self._render_table()

    def _render_table(self) -> None:
        rows = self._all_products
        if self._low_only:
            rows = [product for product in rows if product.is_below_critical_stock]
        self._table.set_products(rows)
