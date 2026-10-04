"""Inventory page - recreates Inventory.dc.html's layout: a KPI row, then
a product table with a row-click detail panel.

Real vs. placeholder, explicitly:
- KPI row (Active SKUs / Units on hand / Stock value): REAL, computed
  from database.product_repository.list_all().
- Product table + Add/Edit/Delete: REAL - reuses the exact same
  ProductFormPopup/product_repository calls as the original
  ProductManagementTab (see that file), just re-skinned into the
  mockup's table+detail-panel layout instead of a table+button-row one.
- Export Price List: REAL, a plain CSV of barcode/name/price/stock - not
  the mockup's presumably branded PDF price sheet, but real data out to
  a real file.
- Row-click detail panel: REAL for what it shows (price/stock/reorder/
  status), but the mockup's "Total / Warehouses / Dealerships" split and
  "share of network stock" bar chart are dropped - there's no
  per-location stock model yet, and this page would rather show nothing
  than a bar chart of fabricated numbers. See architecture.md.
- Inline price editing: the mockup edits price directly in the table
  cell (click a pencil icon, type, blur to save). This slice reuses the
  existing ProductFormPopup instead (click a row, then Edit in the
  detail panel) rather than building a custom cell-editing delegate -
  same underlying product_repository.update() call, less new GUI
  plumbing. A true inline cell editor can replace this later without
  touching the data layer.
"""

from __future__ import annotations

import csv

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.inventory_table import (
    InventoryTable, InventoryTableModel, cost_text, margin_color, margin_text, status_for,
)
from admin_app.gui.components.product_form_popup import ProductFormPopup
from admin_app.gui.components.section import Section
from shared.costing import unit_margin_percent
from shared.formatting import format_int
from shared.currency import format_money
from shared.i18n import enum_label, tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import product_repository, stock_repository
from database.exceptions import DATABASE_ERRORS, DataAccessError
from shared.models import Product

# What the "Show" filter offers: (key, label, predicate).
FILTER_ALL, FILTER_ACTIVE, FILTER_INACTIVE = "all", "active", "inactive"


def filter_products(products: list[Product], mode: str) -> list[Product]:
    """The products the table shows for a Show-filter value (pure, testable)."""
    if mode == FILTER_ACTIVE:
        return [p for p in products if p.is_active]
    if mode == FILTER_INACTIVE:
        return [p for p in products if not p.is_active]
    return list(products)


class _PageTableModel(InventoryTableModel):
    """The inventory table model, with deactivated products dimmed and
    badged "(inactive)" - they stay listed (history, stock) but are plainly
    out of use."""

    def data(self, index, role=Qt.DisplayRole):
        value = super().data(index, role)
        if not index.isValid():
            return value
        product = self.product_at(index.row())
        if product.is_active:
            return value
        if role == Qt.DisplayRole and index.column() == 1:
            return f"{value}  ({tr('admin.inactive_badge')})"
        if role == Qt.ForegroundRole:
            return QColor("#8a8a8a")
        return value


class _PageTable(InventoryTable):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._table_model = _PageTableModel(self)
        self.setModel(self._table_model)
        header = self.horizontalHeader()  # a new model re-initialises the header's section modes
        header.setStretchLastSection(True)
        header.setSectionResizeMode(1, QHeaderView.Stretch)


class InventoryPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.inventory.title"), parent, subtitle=tr("page.inventory.subtitle"))

        export_button = CompactButton(tr("admin.inventory.export_price"))
        export_button.clicked.connect(self._export_price_list)
        self.add_header_action(export_button)

        add_button = CompactButton(tr("admin.inventory.add"), variant="primary")
        add_button.clicked.connect(self._open_add_popup)
        self.add_header_action(add_button)

        self._popup = ProductFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        self._all_products: list[Product] = []

        self.body_layout().addWidget(self._build_kpi_row())

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        table_section = Section(tr("admin.inventory.kicker"), tr("admin.inventory.heading"))
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 4, 0, 8)
        filter_row.setSpacing(10)
        filter_row.addWidget(QLabel(tr("admin.inventory_show")))
        self._filter_combo = QComboBox()
        self._filter_combo.addItem(tr("admin.inventory_show_all"), FILTER_ALL)
        self._filter_combo.addItem(tr("admin.inventory_show_active"), FILTER_ACTIVE)
        self._filter_combo.addItem(tr("admin.inventory_show_inactive"), FILTER_INACTIVE)
        self._filter_combo.currentIndexChanged.connect(lambda _i: self._apply_filter())
        self._filter_combo.setMinimumWidth(170)
        self._filter_combo.setMinimumHeight(30)
        filter_row.addWidget(self._filter_combo)
        filter_row.addStretch(1)
        table_section.body_layout().addLayout(filter_row)
        self._table = _PageTable()
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
        layout.setSpacing(40)

        self._sku_count_label, sku_container = self._kpi_stat(tr("admin.inventory.kpi_skus"))
        self._units_label, units_container = self._kpi_stat(tr("admin.inventory.kpi_units"))
        self._value_label, value_container = self._kpi_stat(tr("admin.inventory.kpi_value"))
        for container in (sku_container, units_container, value_container):
            layout.addWidget(container)
        layout.addStretch(1)
        return row

    def _kpi_stat(self, label_text: str) -> tuple[QLabel, QWidget]:
        p = CLASSICAL_PALETTE
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        kicker = QLabel(label_text.upper())
        kicker.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        value = QLabel("—")
        value.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 32px; color: {p['text_primary']};")
        col.addWidget(kicker)
        col.addWidget(value)
        return value, container

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

        self._detail_sku = QLabel()
        self._detail_sku.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self._detail_sku)

        self._detail_rows_container = QVBoxLayout()
        self._detail_rows_container.setSpacing(6)
        layout.addLayout(self._detail_rows_container)

        note = QLabel(tr("admin.inventory_detail_note"))
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(note)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        self._detail_edit_button = CompactButton(tr("admin.inventory.edit"))
        self._detail_edit_button.clicked.connect(self._open_edit_popup)
        self._detail_active_button = CompactButton(tr("admin.deactivate"))
        self._detail_active_button.clicked.connect(self._toggle_active_selected)
        self._detail_delete_button = CompactButton(tr("admin.inventory.delete"))
        self._detail_delete_button.clicked.connect(self._delete_selected)
        button_row.addWidget(self._detail_edit_button)
        button_row.addWidget(self._detail_active_button)
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

    def _show_detail(self, product: Product | None) -> None:
        while self._detail_rows_container.count():
            item = self._detail_rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_selection = product is not None
        self._detail_edit_button.setEnabled(has_selection)
        self._detail_delete_button.setEnabled(has_selection)
        self._detail_active_button.setEnabled(has_selection)
        if product is not None and not product.is_active:
            self._detail_active_button.setText(tr("admin.reactivate"))
        else:
            self._detail_active_button.setText(tr("admin.deactivate"))

        if product is None:
            self._detail_name.setText(tr("admin.inventory.none_selected"))
            self._detail_sku.setText(tr("admin.inventory.click_row"))
            return

        self._detail_name.setText(product.name if product.is_active else f"{product.name} ({tr('admin.inactive_badge')})")
        self._detail_sku.setText(product.barcode)

        status_label, status_color = status_for(product)
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.inventory.base_price"), format_money(product.price, "$")))
        self._detail_rows_container.addWidget(self._detail_row(
            tr("admin.inventory.unit_cost"),
            cost_text(product, "$") if product.cost_known else tr("admin.inventory.cost_unknown")))
        self._detail_rows_container.addWidget(
            self._detail_row(tr("admin.inventory.margin"), margin_text(product), color=margin_color(product)))
        self._detail_rows_container.addWidget(
            self._detail_row(tr("admin.inventory_total_network"), str(product.stock_quantity)))
        # Where those units are (per-location stock - see stock_repository).
        try:
            levels = stock_repository.levels_for_product(product.barcode)
        except DATABASE_ERRORS:
            levels = []
        placed = 0
        for level in levels:
            kind = enum_label("location_type", {"warehouse": "Warehouse", "dealership": "Dealership"}.get(level.location.kind, ""))
            label = tr("admin.inventory.loc_row").format(kind=kind, code=level.location.code).rstrip() if kind else tr("admin.inventory.unassigned")
            self._detail_rows_container.addWidget(self._detail_row(label, str(level.quantity)))
            placed += level.quantity
        if product.stock_quantity > placed:
            self._detail_rows_container.addWidget(self._detail_row(tr("admin.inventory.on_road"), str(product.stock_quantity - placed)))
        self._detail_rows_container.addWidget(self._detail_row(tr("admin.inventory.reorder_at"), str(product.critical_stock_level)))
        if not product.is_active:
            status_label, status_color = tr("admin.inactive_badge").capitalize(), CLASSICAL_PALETTE["text_secondary"]
        self._detail_rows_container.addWidget(
            self._detail_row(tr("admin.inventory_status_network"), status_label, color=status_color))

    def _on_selection_changed(self) -> None:
        self._show_detail(self._table.selected_product())

    # --- Data + actions --------------------------------------------------

    def reload(self) -> None:
        try:
            self._all_products = product_repository.list_all()
        except DATABASE_ERRORS:
            self._all_products = []
        self._apply_filter()
        active = [p for p in self._all_products if p.is_active]
        self._sku_count_label.setText(str(len(active)))
        self._units_label.setText(format_int(sum(p.stock_quantity for p in self._all_products)))
        self._value_label.setText(
            format_money(sum(p.stock_quantity * p.price for p in self._all_products), "$")
        )

    def _apply_filter(self) -> None:
        """Re-fill the table for the Show filter, keeping the selection if the product is still listed."""
        keep = self._table.selected_product()
        shown = filter_products(self._all_products, self._filter_combo.currentData() or FILTER_ALL)
        self._table.set_products(shown)
        if keep is not None:
            for row, product in enumerate(shown):
                if product.barcode == keep.barcode:
                    self._table.selectRow(row)
                    return
        self._show_detail(None)

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(product=None)

    def _open_edit_popup(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.inventory.none_selected"), tr("admin.inventory.select_first"))
            return
        self._popup.open_or_refresh(product=product)

    def _save_popup(self) -> None:
        product = self._popup.result_product()
        editing = self._popup.is_editing()
        try:
            if editing:
                product_repository.update(product)
                if self._popup.cost_changed():
                    product_repository.set_cost(product.barcode, product.cost_price)
            else:
                product_repository.create(product)
        except (ValueError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.inventory.save_failed"), str(exc))
            # Re-open with what was typed so nothing has to be re-entered.
            original = next((p for p in self._all_products if p.barcode == product.barcode), None) if editing else None
            self._popup.open_or_refresh(product=original, draft=product)
            return
        self.reload()

    def _toggle_active_selected(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.inventory.none_selected"), tr("admin.inventory.select_first"))
            return
        try:
            product_repository.set_active(product.barcode, not product.is_active)
        except (ValueError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.inventory.save_failed"), str(exc))
            return
        self.reload()

    def _delete_selected(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, tr("admin.inventory.none_selected"), tr("admin.inventory.select_first"))
            return

        confirm = QMessageBox.question(
            self, tr("admin.inventory.delete_title"), tr("admin.inventory.delete_confirm").format(name=product.name)
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            product_repository.delete(product.barcode)
        except (ValueError, *DATABASE_ERRORS) as exc:
            # e.g. ProductInUseError: it has stock or history - the message says to deactivate instead.
            QMessageBox.warning(self, tr("admin.inventory.delete_failed"), str(exc))
            return
        self.reload()
        self._show_detail(None)

    def _export_price_list(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(
            self, tr("admin.inventory.export_title"), "price_list.csv", tr("admin.inventory.csv_filter")
        )
        if not path_str:
            return
        try:
            with open(path_str, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Barcode", "Name", "Price", "Stock", "Reorder At", "Cost", "Margin %"])
                for product in self._all_products:
                    margin = unit_margin_percent(product.price, product.cost_price)
                    writer.writerow(
                        [product.barcode, product.name, f"{product.price:.2f}", product.stock_quantity,
                         product.critical_stock_level,
                         f"{product.cost_price:.2f}" if product.cost_known else "",
                         f"{margin:.1f}" if margin is not None else ""]
                    )
        except OSError as exc:
            QMessageBox.warning(self, tr("admin.inventory.export_failed"), str(exc))
            return
        QMessageBox.information(self, tr("admin.export_done_title"), tr("admin.export_done_body").format(path=path_str))
