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
from admin_app.gui.components.reorder_levels_popup import ReorderLevelsPopup
from admin_app.gui.components.section import Section
from admin_app.gui.components.stock_move_popup import StockMovePopup
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from shared import current_session
from shared.formatting import format_int
from shared.i18n import plural, region_label, tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import dealership_repository, product_repository, stock_repository, warehouse_repository
from database.exceptions import DATABASE_ERRORS
from shared.models import DEALERSHIP_REGIONS, Dealership, StockLocation


class DealershipsPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.dealerships.title"), parent, subtitle=tr("page.dealerships.subtitle"))

        add_button = CompactButton(tr("admin.dealerships.add"), variant="primary")
        add_button.clicked.connect(self._open_add_popup)
        self.add_header_action(add_button)

        self._popup = DealershipFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        self._reorder_popup = ReorderLevelsPopup(self)
        self._reorder_popup.levels_changed.connect(self.reload)
        self._count_popup = StockMovePopup(self)
        self._count_popup.stock_changed.connect(self.reload)

        self._all_dealerships: list[Dealership] = []

        self.body_layout().addWidget(self._build_kpi_row())

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        table_section = Section(tr("admin.dealerships.kicker"), tr("admin.dealerships.directory"))
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

        self._total_card = StatCard(tr("admin.dealerships.kpi_total"), "—")
        self._active_card = StatCard(tr("admin.dealerships.kpi_active"), "—")

        self._region_card = StatCard(tr("admin.dealerships.kpi_region"), "—")
        self._region_breakdown_items: dict[str, QWidget] = {}
        for region in DEALERSHIP_REGIONS:
            item = stat_breakdown_item(region_label(region), "0")
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
        self._detail_name.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(self._detail_name)

        self._detail_code = QLabel()
        self._detail_code.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self._detail_code)

        self._detail_rows_container = QVBoxLayout()
        self._detail_rows_container.setSpacing(6)
        layout.addLayout(self._detail_rows_container)

        note = QLabel(tr("admin.dealerships.detail_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(note)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        self._detail_edit_button = CompactButton(tr("admin.dealerships.edit"))
        self._detail_edit_button.clicked.connect(self._open_edit_popup)
        self._detail_delete_button = CompactButton(tr("admin.dealerships.delete"))
        self._detail_delete_button.clicked.connect(self._delete_selected)
        button_row.addWidget(self._detail_edit_button)
        button_row.addWidget(self._detail_delete_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        stock_row = QHBoxLayout()
        self._detail_levels_button = CompactButton(tr("admin.dealerships.reorder_levels"))
        self._detail_levels_button.clicked.connect(self._open_reorder_levels)
        self._detail_opening_button = CompactButton(tr("admin.dealerships.opening_stock"))
        self._detail_opening_button.clicked.connect(self._open_opening_stock)
        stock_row.addWidget(self._detail_levels_button)
        stock_row.addWidget(self._detail_opening_button)
        stock_row.addStretch(1)
        layout.addLayout(stock_row)

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
        self._detail_levels_button.setEnabled(has_selection)
        self._detail_opening_button.setEnabled(has_selection)

        if dealership is None:
            self._detail_name.setText(tr("admin.dealerships.none_selected"))
            self._detail_code.setText(tr("admin.dealerships.click_row"))
            return

        self._detail_name.setText(dealership.name)
        self._detail_code.setText(dealership.code)

        status_label, status_color = status_for(dealership)
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.dealerships.col_region"), region_label(dealership.region)))
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.dealerships.col_city"), dealership.city))
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.dealerships.col_manager"), dealership.manager_name or "—"))
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.dealerships.col_status"), status_label, color=status_color))
        # Real stock on this dealership's shelves (per-location stock).
        try:
            levels = stock_repository.levels_at(StockLocation.dealership(dealership.code))
        except DATABASE_ERRORS:
            levels = []
        units = sum(level.quantity for level in levels)
        self._detail_rows_container.addWidget(
            self._detail_row(
                tr("admin.dealerships.stock_on_hand"),
                tr("admin.dealerships.units_skus").format(units=format_int(units), skus=plural("common.sku", len(levels))),
            )
        )

    def _on_selection_changed(self) -> None:
        self._show_detail(self._table.selected_dealership())

    # --- Data + actions --------------------------------------------------

    def reload(self) -> None:
        """Re-read the list (MainWindow calls this each time the page is
        shown). The selected dealership stays selected - with a fresh
        detail panel - if it still exists; otherwise the panel is cleared."""
        selected = self._table.selected_dealership()
        try:
            self._all_dealerships = dealership_repository.list_all()
        except DATABASE_ERRORS:
            self._all_dealerships = []
        self._table.set_dealerships(self._all_dealerships)
        self.show_dealership(selected.code if selected else None)

        self._total_card.set_value(str(len(self._all_dealerships)))
        active_count = sum(1 for d in self._all_dealerships if d.is_active)
        self._active_card.set_value(str(active_count))

        for region in DEALERSHIP_REGIONS:
            count = sum(1 for d in self._all_dealerships if d.region == region)
            item = self._region_breakdown_items[region]
            item.layout().itemAt(1).widget().setText(str(count))

    def show_dealership(self, code: str | None) -> None:
        """Select `code`'s row and refresh the detail panel from the
        current list, or clear the panel (None / no longer there)."""
        if code and self._table.select_code(code):
            self._show_detail(self._table.selected_dealership())
        else:
            self._table.clearSelection()
            self._show_detail(None)

    def _open_reorder_levels(self) -> None:
        dealership = self._table.selected_dealership()
        if dealership is not None:
            self._reorder_popup.open_for(StockLocation.dealership(dealership.code), dealership.name)

    def _open_opening_stock(self) -> None:
        """Type what this shop really holds (the count form, already on this shop): units that were waiting
        unplaced are placed here, anything beyond is new stock."""
        dealership = self._table.selected_dealership()
        if dealership is None:
            return
        try:
            self._count_popup.set_choices(product_repository.list_active(), warehouse_repository.list_all(active_only=True),
                                          self._all_dealerships, source=StockLocation.dealership(dealership.code))
        except DATABASE_ERRORS:
            return
        self._count_popup.show_count()

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(dealership=None)

    def _open_edit_popup(self) -> None:
        dealership = self._table.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, tr("admin.dealerships.none_selected"), tr("admin.dealerships.select_first"))
            return
        self._popup.open_or_refresh(dealership=dealership)

    def _save_popup(self) -> None:
        dealership = self._popup.result_dealership()
        try:
            if self._popup.is_editing():
                dealership_repository.update(dealership)
            else:
                dealership_repository.create(dealership)
        except (*DATABASE_ERRORS, ValueError) as exc:
            self._popup.show_error(str(exc))  # popup comes back with everything typed
            return
        self.reload()
        self.show_dealership(dealership.code)  # fresh detail for the one just saved

    def _delete_selected(self) -> None:
        dealership = self._table.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, tr("admin.dealerships.none_selected"), tr("admin.dealerships.select_first"))
            return

        confirm = QMessageBox.question(
            self, tr("admin.dealerships.delete_title"), tr("admin.dealerships.delete_confirm").format(name=dealership.name)
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            dealership_repository.delete(dealership.code, current_session.actor())
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("admin.dealerships.delete_failed"), str(exc))
            return
        self.reload()
        self.show_dealership(None)
