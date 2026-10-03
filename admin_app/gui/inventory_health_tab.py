"""Passive, on-demand view of products at/below critical stock.

Deliberately the Admin counterpart to depot_app/gui/low_stock_panel.py -
same query (product_repository.get_critical_stock_list()), same table
component (admin_app/gui/components/data_table.py), but no
shared.gui_kit.polling.PollingTimer here and no prominence in the tab
order: a manager opens this tab to review the overall picture, rather
than having it pushed at them. Real-time operational response to a
low-stock condition is Depot's job (see architecture.md's Alert
Mechanics section for the full rationale).
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QWidget

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.data_table import DataTable
from database import product_repository
from shared.gui_kit.visual_tab import VisualTab
from shared.i18n import tr


class InventoryHealthTab(VisualTab):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._table = DataTable()
        self.set_visual(self._table)

        # A manual refresh, not a PollingTimer: this tab is deliberately
        # NOT operationally live - see the module docstring.
        refresh_button = CompactButton(tr("admin.refresh"))
        refresh_button.clicked.connect(self.refresh)

        controls = QWidget()
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.addWidget(refresh_button)
        controls_layout.addStretch()
        self.set_controls(controls)

        self.refresh()

    def refresh(self) -> None:
        self._table.set_products(product_repository.get_critical_stock_list())
