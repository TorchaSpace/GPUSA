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

from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.inventory_table import InventoryTable, status_for
from admin_app.gui.components.product_form_popup import ProductFormPopup
from admin_app.gui.components.section import Section
from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import product_repository, stock_repository
from database.exceptions import DataAccessError
from shared.models import Product


class InventoryPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.inventory.title"), parent, subtitle=tr("page.inventory.subtitle"))

        export_button = CompactButton("Export Price List")
        export_button.clicked.connect(self._export_price_list)
        self.add_header_action(export_button)

        add_button = CompactButton("Add Product", variant="primary")
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

        table_section = Section("Stock", "Product inventory")
        self._table = InventoryTable()
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

        self._sku_count_label, sku_container = self._kpi_stat("Active SKUs")
        self._units_label, units_container = self._kpi_stat("Units on hand")
        self._value_label, value_container = self._kpi_stat("Stock value")
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

        note = QLabel(
            "Per-warehouse and per-dealership stock breakdown isn't tracked yet - "
            "totals above are network-wide."
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

    def _show_detail(self, product: Product | None) -> None:
        while self._detail_rows_container.count():
            item = self._detail_rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_selection = product is not None
        self._detail_edit_button.setEnabled(has_selection)
        self._detail_delete_button.setEnabled(has_selection)

        if product is None:
            self._detail_name.setText("No product selected")
            self._detail_sku.setText("Click a row to see its detail.")
            return

        self._detail_name.setText(product.name)
        self._detail_sku.setText(product.barcode)

        status_label, status_color = status_for(product)
        self._detail_rows_container.addWidget(self._detail_row("Base price", f"${product.price:,.2f}"))
        self._detail_rows_container.addWidget(self._detail_row("Total stock", str(product.stock_quantity)))
        # Where those units are (per-location stock - see stock_repository).
        try:
            levels = stock_repository.levels_for_product(product.barcode)
        except DataAccessError:
            levels = []
        placed = 0
        for level in levels:
            kind = {"warehouse": "Warehouse", "dealership": "Dealership"}.get(level.location.kind, "")
            label = f"  {kind} {level.location.code}".rstrip() if kind else "  Unassigned"
            self._detail_rows_container.addWidget(self._detail_row(label, str(level.quantity)))
            placed += level.quantity
        if product.stock_quantity > placed:
            self._detail_rows_container.addWidget(self._detail_row("  On the road", str(product.stock_quantity - placed)))
        self._detail_rows_container.addWidget(self._detail_row("Reorder at", str(product.critical_stock_level)))
        self._detail_rows_container.addWidget(self._detail_row("Status", status_label, color=status_color))

    def _on_selection_changed(self) -> None:
        self._show_detail(self._table.selected_product())

    # --- Data + actions --------------------------------------------------

    def reload(self) -> None:
        try:
            self._all_products = product_repository.list_all()
        except DataAccessError:
            self._all_products = []
        self._table.set_products(self._all_products)
        self._sku_count_label.setText(str(len(self._all_products)))
        self._units_label.setText(f"{sum(p.stock_quantity for p in self._all_products):,}")
        self._value_label.setText(
            f"${sum(p.stock_quantity * p.price for p in self._all_products):,.2f}"
        )

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(product=None)

    def _open_edit_popup(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, "No product selected", "Select a product in the table first.")
            return
        self._popup.open_or_refresh(product=product)

    def _save_popup(self) -> None:
        product = self._popup.result_product()
        try:
            if self._popup.is_editing():
                product_repository.update(product)
            else:
                product_repository.create(product)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()

    def _delete_selected(self) -> None:
        product = self._table.selected_product()
        if product is None:
            QMessageBox.information(self, "No product selected", "Select a product in the table first.")
            return

        confirm = QMessageBox.question(
            self, "Delete product?", f"Delete {product.name}? This cannot be undone."
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            product_repository.delete(product.barcode)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()
        self._show_detail(None)

    def _export_price_list(self) -> None:
        path_str, _ = QFileDialog.getSaveFileName(self, "Export Price List", "price_list.csv", "CSV Files (*.csv)")
        if not path_str:
            return
        try:
            with open(path_str, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["Barcode", "Name", "Price", "Stock", "Reorder At"])
                for product in self._all_products:
                    writer.writerow(
                        [product.barcode, product.name, f"{product.price:.2f}", product.stock_quantity, product.critical_stock_level]
                    )
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Export complete", f"Saved to {path_str}")
