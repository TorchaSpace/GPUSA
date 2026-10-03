"""Dealerships page - the real v1 build of the Dealerships domain,
replacing its themed placeholder. Mirrors inventory_page.py's shape: a
KPI row, then a table with a row-click detail panel and Add/Edit/Delete.

Real vs. deliberately left out, explicitly (see dealership_repository.py's
module docstring and architecture.md's "New data domains" note):
- KPI row (Total dealerships / Active / by-region breakdown): REAL,
  computed from database.dealership_repository.list_all().
- Table + Add/Edit/Delete: REAL, via DealershipFormPopup/
  dealership_repository, same pattern as InventoryPage/ProductFormPopup.
- Revenue·MTD, 7-day trend/sparkline, staff roster, and per-dealership
  stock-on-hand - all fabricated client-side in the mockup's own script
  (sine wave + hashed pseudo-random), not derived from anything the
  mockup actually persists. Shown nowhere here, same call InventoryPage's
  own detail panel already made for its dropped bar chart - this page
  would rather show nothing than a chart of fabricated numbers.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.dealership_form_popup import DealershipFormPopup
from admin_app.gui.components.dealership_table import DealershipTable, status_for
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING
from database import dealership_repository, stock_repository
from database.exceptions import DataAccessError
from shared.models import DEALERSHIP_REGIONS, Dealership, StockLocation


class DealershipsPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Dealership Network", parent)

        add_button = CompactButton("Add Dealership")
        add_button.clicked.connect(self._open_add_popup)
        self.add_header_action(add_button)

        self._popup = DealershipFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        self._all_dealerships: list[Dealership] = []

        self.body_layout().addWidget(self._build_kpi_row())

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        table_section = Section("Accounts", "Dealership directory")
        self._table = DealershipTable()
        self._table.setMinimumHeight(380)
        self._table.doubleClicked.connect(lambda _index: self._open_edit_popup())
        table_section.body_layout().addWidget(self._table)
        content_layout.addWidget(table_section, stretch=2)

        self._detail_panel = self._build_detail_panel()
        content_layout.addWidget(self._detail_panel, stretch=1)

        self.body_layout().addWidget(content, stretch=1)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.reload()
        self._show_detail(None)

    # --- KPI row -----------------------------------------------------

    def _build_kpi_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._total_card = StatCard("Total Dealerships", "—")
        self._active_card = StatCard("Active", "—")

        self._region_card = StatCard("By Region", "—")
        self._region_breakdown_items: dict[str, QWidget] = {}
        for region in DEALERSHIP_REGIONS:
            item = stat_breakdown_item(region, "0")
            self._region_breakdown_items[region] = item
            self._region_card.footer_layout().addWidget(item)

        for card in (self._total_card, self._active_card, self._region_card):
            layout.addWidget(card, stretch=1)
        return row

    # --- Detail panel --------------------------------------------------

    def _build_detail_panel(self) -> QFrame:
        p = CLASSICAL_PALETTE
        panel = QFrame()
        panel.setStyleSheet(
            f"""
            QFrame {{
                background-color: {p['background']};
                border: 1px solid {p['border']};
                border-radius: {p['radius_md']};
            }}
            QLabel {{ border: none; }}
            """
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._detail_name = QLabel()
        self._detail_name.setWordWrap(True)
        self._detail_name.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(self._detail_name)

        self._detail_code = QLabel()
        self._detail_code.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self._detail_code)

        self._detail_rows_container = QVBoxLayout()
        self._detail_rows_container.setSpacing(6)
        layout.addLayout(self._detail_rows_container)

        note = QLabel(
            "Revenue, staff roster, and per-dealership stock-on-hand aren't tracked yet - "
            "the mockup fabricates those figures rather than persisting them."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(note)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        self._detail_edit_button = CompactButton("Edit")
        self._detail_edit_button.clicked.connect(self._open_edit_popup)
        self._detail_delete_button = CompactButton("Delete")
        self._detail_delete_button.clicked.connect(self._delete_selected)
        button_row.addWidget(self._detail_edit_button)
        button_row.addWidget(self._detail_delete_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        return panel

    def _detail_row(self, label: str, value: str, color: str | None = None) -> QWidget:
        p = CLASSICAL_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label_widget = QLabel(label)
        label_widget.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        value_widget = QLabel(value)
        value_widget.setStyleSheet(f"font-size: 12px; color: {color or p['text_primary']};")
        layout.addWidget(label_widget)
        layout.addStretch(1)
        layout.addWidget(value_widget)
        return row

    def _show_detail(self, dealership: Dealership | None) -> None:
        while self._detail_rows_container.count():
            item = self._detail_rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_selection = dealership is not None
        self._detail_edit_button.setEnabled(has_selection)
        self._detail_delete_button.setEnabled(has_selection)

        if dealership is None:
            self._detail_name.setText("No dealership selected")
            self._detail_code.setText("Click a row to see its detail.")
            return

        self._detail_name.setText(dealership.name)
        self._detail_code.setText(dealership.code)

        status_label, status_color = status_for(dealership)
        self._detail_rows_container.addWidget(self._detail_row("Region", dealership.region))
        self._detail_rows_container.addWidget(self._detail_row("City", dealership.city))
        self._detail_rows_container.addWidget(self._detail_row("Manager", dealership.manager_name or "—"))
        self._detail_rows_container.addWidget(self._detail_row("Status", status_label, color=status_color))
        # Real stock on this dealership's shelves (per-location stock).
        try:
            levels = stock_repository.levels_at(StockLocation.dealership(dealership.code))
        except DataAccessError:
            levels = []
        units = sum(level.quantity for level in levels)
        self._detail_rows_container.addWidget(
            self._detail_row("Stock on hand", f"{units:,} units · {len(levels)} SKU{'s' if len(levels) != 1 else ''}")
        )

    def _on_selection_changed(self) -> None:
        self._show_detail(self._table.selected_dealership())

    # --- Data + actions --------------------------------------------------

    def reload(self) -> None:
        try:
            self._all_dealerships = dealership_repository.list_all()
        except DataAccessError:
            self._all_dealerships = []
        self._table.set_dealerships(self._all_dealerships)

        self._total_card.set_value(str(len(self._all_dealerships)))
        active_count = sum(1 for d in self._all_dealerships if d.is_active)
        self._active_card.set_value(str(active_count))

        for region in DEALERSHIP_REGIONS:
            count = sum(1 for d in self._all_dealerships if d.region == region)
            item = self._region_breakdown_items[region]
            item.layout().itemAt(1).widget().setText(str(count))

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(dealership=None)

    def _open_edit_popup(self) -> None:
        dealership = self._table.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, "No dealership selected", "Select a dealership in the table first.")
            return
        self._popup.open_or_refresh(dealership=dealership)

    def _save_popup(self) -> None:
        dealership = self._popup.result_dealership()
        try:
            if self._popup.is_editing():
                dealership_repository.update(dealership)
            else:
                dealership_repository.create(dealership)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()

    def _delete_selected(self) -> None:
        dealership = self._table.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, "No dealership selected", "Select a dealership in the table first.")
            return

        confirm = QMessageBox.question(
            self, "Delete dealership?", f"Delete {dealership.name}? This cannot be undone."
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            dealership_repository.delete(dealership.code)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()
        self._show_detail(None)
