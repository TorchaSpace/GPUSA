"""Console > Inventory - this warehouse's stock, replacing the page's
themed placeholder (the mockup lists the page but never drew it).

One row per product: SKU, product, on hand HERE, reorder level, status
(Out / Low / OK here), how many units the rest of the company holds
(everywhere else + on trucks), and when stock last moved here. Search by
SKU or name; filter All / Held here / Low / Out.

"Count selected…" records a stock count at this warehouse: the manager
types what's really on the shelf, the level is set to it, and the
difference is logged as a 'count' movement under their name (this page
is behind the Console's manager sign-in). Product details and prices are
edited in Admin > Inventory; moving stock between places is Admin's
"Move / count stock", or a shipment.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from database import product_repository, stock_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.components.industry_widgets import AMBER, industry_table, item, kicker
from depot_app.theme import INDUSTRY_PALETTE
from shared import current_session
from shared.formatting import local_datetime_text
from shared.models import Product, Warehouse
from shared.warehousing import tr_or

FILTERS = ("All", "Held here", "Low", "Out")


def status_here(product: Product) -> str:
    """Out / Low / OK for THIS warehouse. A product it has never stocked
    (no stock level row here) is "Not stocked", not "Out" - there is
    nothing to run out of, and it must not count in the Out / Low filters."""
    if product.stock_quantity <= 0 and not product.stocked_here:
        return "Not stocked"
    if product.stock_quantity <= 0:
        return "Out"
    if product.is_below_critical_stock:
        return "Low"
    return "OK"


class InventoryPage(QWidget):
    def __init__(self, warehouse: Warehouse, parent: QWidget | None = None):
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self.warehouse = warehouse
        self._filter = "All"
        self._products: list[Product] = []
        self._totals: dict[str, int] = {}
        self._last: dict[str, str] = {}
        self._shown: list[Product] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(kicker(f"Stock at {warehouse.site_label}"), stretch=1)
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        top.addWidget(self.summary_label)
        layout.addLayout(top)

        bar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search SKU or product")
        self.search_input.setStyleSheet(
            f"background-color: {p['surface_raised']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
            f"padding: 6px 8px; font-size: 14px;"
        )
        self.search_input.textChanged.connect(self._render)
        bar.addWidget(self.search_input, stretch=1)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self.filter_buttons: dict[str, QPushButton] = {}
        for name in FILTERS:
            button = QPushButton(name)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ border: 1px solid {p['border']}; background: {p['background']}; color: {p['text_secondary']}; "
                f"padding: 6px 12px; font-size: 12px; }}"
                f"QPushButton:checked {{ background: {p['accent_100']}; color: {p['accent_900']}; border-color: {p['accent']}; }}"
            )
            button.clicked.connect(lambda _c=False, n=name: self.set_filter(n))
            group.addButton(button)
            bar.addWidget(button)
            self.filter_buttons[name] = button
        self.filter_buttons["All"].setChecked(True)
        self.count_button = IndustryButton("Count selected…", variant="primary")
        self.count_button.clicked.connect(self.count_selected)
        bar.addWidget(self.count_button)
        layout.addLayout(bar)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self.message)

        self.table = industry_table(["SKU", "Product", "On hand here", "Reorder at", "Status", "Rest of company",
                                     "Last moved here"], selectable=True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._sync)
        layout.addWidget(self.table, stretch=1)
        self._sync()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()

    def reload(self) -> None:
        try:
            self._products = stock_repository.products_at(self.warehouse.location)
            self._totals = {pr.barcode: pr.stock_quantity for pr in product_repository.list_all()}
            self._last = stock_repository.last_movement_at(self.warehouse.location)
        except DATABASE_ERRORS as exc:
            self.message.setText(f"Couldn't load stock: {exc}")
            return
        held = [pr for pr in self._products if pr.stock_quantity > 0]
        self.summary_label.setText(f"{len(held)} SKUs held · {sum(pr.stock_quantity for pr in held):,} units")
        self._render()

    def set_filter(self, name: str) -> None:
        self._filter = name
        self.filter_buttons[name].setChecked(True)
        self._render()

    def _visible(self) -> list[Product]:
        text = self.search_input.text().strip().casefold()
        rows = self._products
        if text:
            rows = [pr for pr in rows if text in pr.barcode.casefold() or text in pr.name.casefold()]
        if self._filter == "Held here":
            rows = [pr for pr in rows if pr.stock_quantity > 0]
        elif self._filter == "Low":
            rows = [pr for pr in rows if status_here(pr) == "Low"]
        elif self._filter == "Out":
            rows = [pr for pr in rows if status_here(pr) == "Out"]
        return rows

    def _render(self) -> None:
        selected = self.selected()
        self._shown = self._visible()
        self.table.setRowCount(len(self._shown))
        for r, pr in enumerate(self._shown):
            status = status_here(pr)
            color = AMBER if status == "Low" else "#b07f00" if status == "Out" else None
            last = self._last.get(pr.barcode)
            for c, cell in enumerate([
                item(pr.barcode),
                item(pr.name if pr.is_active else f"{pr.name} ({tr_or('admin.inactive_badge', 'inactive')})"),
                item(f"{pr.stock_quantity:,}", right=True),
                item(pr.critical_stock_level, right=True), item(status, color=color),
                item(f"{self._totals.get(pr.barcode, 0) - pr.stock_quantity:,}", right=True),
                item(local_datetime_text(last) if last else "—"),
            ]):
                self.table.setItem(r, c, cell)
        if selected is not None:
            self.select(selected.barcode)
        self._sync()

    def selected(self) -> Product | None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows or rows[0].row() >= len(self._shown):
            return None
        return self._shown[rows[0].row()]

    def select(self, barcode: str) -> None:
        for r, pr in enumerate(self._shown):
            if pr.barcode == barcode:
                self.table.selectRow(r)
                return

    def _sync(self) -> None:
        self.count_button.setEnabled(self.selected() is not None)

    def record_count(self, barcode: str, counted: int, note: str | None = None) -> int:
        """Set this warehouse's level of `barcode` to `counted` under the
        signed-in manager's name; returns the difference."""
        diff = stock_repository.set_count(self.warehouse.location, barcode, counted, note, actor=current_session.actor())
        self.message.setText(
            f"{barcode}: count matches ({counted:,})." if diff == 0 else f"{barcode}: set to {counted:,} ({diff:+,}), logged as a stock count."
        )
        self.reload()
        return diff

    def count_selected(self) -> None:
        product = self.selected()
        if product is None:
            return
        counted, ok = QInputDialog.getInt(
            self, "Stock count", f"{product.barcode} · {product.name}\nUnits actually on the shelf here "
            f"(the system says {product.stock_quantity:,}):", product.stock_quantity, 0, 10_000_000)
        if not ok:
            return
        note, _ok = QInputDialog.getText(self, "Stock count", "Note (optional):")
        try:
            self.record_count(product.barcode, counted, note)
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.message.setText(f"Couldn't record the count: {exc}")
