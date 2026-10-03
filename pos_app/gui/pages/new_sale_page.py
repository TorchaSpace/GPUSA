"""New Sale (checkout) screen - recreates the mockup's product grid +
cart panel. This is the concrete demonstration of the "shared database
is the connection" answer: this screen and admin_app's Inventory page
both read/write the exact same `products` table through
database.product_repository - there's no separate sync mechanism,
they're just two GUIs over one database.

Real, end to end:
- Stock is THIS dealership's shelf (database.stock_repository.products_at
  the terminal's dealership - see pos_app/gui/main_window.py): the grid
  shows and caps at what's here, not what the company has elsewhere.
- Product grid: every product with its local quantity, filtered by the
  search box client-side (no `category` field on Product - see
  shared.models - so the mockup's category pills are dropped).
- Adding to cart is capped at the product's live stock_quantity, same
  as the mockup's Math.min(qty+1, p.stock).
- Checkout calls pos_app.services.checkout_service.complete_sale(),
  which calls database.transaction_repository.finalize_transaction()
  (task #56) - a real atomic sale: stock is decremented in the same
  database admin_app's Inventory page reads, so switching to that page
  (or another POS till hitting the same shared_backend.db) shows the
  new numbers immediately, no polling or push needed.

Simplification: the mockup has separate "Card"/"Cash" buttons. Since
shared.models.Transaction has no payment-method field yet, both buttons
finalize the identical sale - the distinction is visual only for now,
not persisted. Extending Transaction with a real payment_method column
is a small, separate follow-up once that's actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from database import stock_repository
from database.exceptions import DataAccessError, InsufficientStockError
from pos_app.gui.components.action_button import ActionButton
from pos_app.gui.product_status import stock_status
from pos_app.services.checkout_service import complete_sale
from pos_app.theme import FONT_HEADING, ORGANIC_PALETTE
from shared.models import UNASSIGNED, LineItem, Product, StockLocation, Transaction
from shared import current_session

_STATUS_COLORS = {"out": ("#d8412f", "white"), "low": ("#f2c230", "#3a2a05")}


@dataclass
class _CartLine:
    barcode: str
    name: str
    unit_price: float
    quantity: int
    max_quantity: int

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)


class NewSalePage(QWidget):
    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._location = location  # this terminal's dealership (UNASSIGNED with no setup identity)
        p = ORGANIC_PALETTE
        self.setStyleSheet(f"background-color: {p['background']};")

        self._all_products: list[Product] = []
        self._cart: dict[str, _CartLine] = {}  # barcode -> _CartLine, insertion order preserved

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 12, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_catalog(), stretch=1)
        layout.addWidget(self._build_cart_panel())

    # --- Catalog (left) ------------------------------------------------

    def _build_catalog(self) -> QWidget:
        p = ORGANIC_PALETTE
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 24, 0)
        col.setSpacing(16)

        search_row = QWidget()
        search_row.setStyleSheet(f"background-color: {p['surface_raised']}; border-radius: 30px;")
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(22, 0, 22, 0)
        search_row.setFixedHeight(60)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search products or scan barcode")
        self._search_input.setStyleSheet(
            f"border: none; background: transparent; font-family: '{p['font_family']}'; font-size: 19px; color: {p['text_primary']};"
        )
        self._search_input.textChanged.connect(self._render_grid)
        search_layout.addWidget(self._search_input)
        col.addWidget(search_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        grid_host = QWidget()
        grid_host.setStyleSheet("background: transparent;")
        self._grid_layout = QGridLayout(grid_host)
        self._grid_layout.setSpacing(14)
        scroll.setWidget(grid_host)
        col.addWidget(scroll, stretch=1)

        return container

    def _render_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        query = self._search_input.text().strip().lower()
        visible = [product for product in self._all_products if query in product.name.lower()]

        columns = 4
        for index, product in enumerate(visible):
            tile = self._build_product_tile(product)
            self._grid_layout.addWidget(tile, index // columns, index % columns)

    def _build_product_tile(self, product: Product) -> QPushButton:
        p = ORGANIC_PALETTE
        status = stock_status(product)
        tile = QPushButton()
        tile.setMinimumHeight(164)
        tile.setCursor(Qt.PointingHandCursor)
        tile.setEnabled(status != "out")
        opacity_style = "" if status != "out" else f"color: {p['text_secondary']};"
        tile.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {p['surface_raised']};
                border: none;
                border-radius: {p['radius_lg']};
                text-align: left;
                padding: 18px;
                {opacity_style}
            }}
            QPushButton:hover {{
                background-color: #fff2eb;
            }}
            QPushButton:disabled {{
                background-color: {p['surface']};
            }}
            """
        )
        tile.clicked.connect(lambda: self._add_to_cart(product))

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top_row = QHBoxLayout()
        initial = QLabel(product.name[:1].upper())
        initial.setFixedSize(56, 56)
        initial.setAlignment(Qt.AlignCenter)
        initial.setStyleSheet(
            "background-color: #fff2eb; border-radius: 28px; "
            f"font-family: '{FONT_HEADING}'; font-size: 24px;"
        )
        top_row.addWidget(initial)
        top_row.addStretch(1)

        badge_bg, badge_fg = _STATUS_COLORS.get(status, ("#eee7db", p["text_secondary"]))
        badge_text = "Out" if status == "out" else f"{product.stock_quantity} left"
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_fg}; border-radius: 999px; "
            f"font-weight: 700; font-size: 13px; padding: 5px 10px;"
        )
        top_row.addWidget(badge)
        layout.addLayout(top_row)

        name_label = QLabel(product.name)
        name_label.setWordWrap(True)
        name_label.setStyleSheet(f"font-weight: 700; font-size: 16px; color: {p['text_primary']};")
        layout.addWidget(name_label)
        layout.addStretch(1)

        price_label = QLabel(f"${product.price:,.2f}")
        price_label.setStyleSheet(f"font-weight: 700; font-size: 19px; color: {p['text_primary']};")
        layout.addWidget(price_label)

        return tile

    # --- Cart (right) --------------------------------------------------

    def _build_cart_panel(self) -> QWidget:
        p = ORGANIC_PALETTE
        panel = QWidget()
        panel.setFixedWidth(392)
        panel.setStyleSheet(f"background-color: {p['surface_raised']};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 22, 24, 12)
        title = QLabel("Current sale")
        title.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 28px; color: {p['text_primary']};")
        header.addWidget(title)
        header.addStretch(1)
        clear_button = QPushButton("Clear")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.setStyleSheet(f"border: none; background: none; font-weight: 600; font-size: 15px; color: #b2622d;")
        clear_button.clicked.connect(self._clear_cart)
        header.addWidget(clear_button)
        header_widget = QWidget()
        header_widget.setLayout(header)
        layout.addWidget(header_widget)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        cart_host = QWidget()
        self._cart_rows_layout = QVBoxLayout(cart_host)
        self._cart_rows_layout.setContentsMargins(16, 0, 16, 0)
        self._cart_rows_layout.addStretch(1)
        scroll.setWidget(cart_host)
        layout.addWidget(scroll, stretch=1)

        footer = QWidget()
        footer.setStyleSheet(f"background-color: {p['surface']};")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 18, 24, 24)
        footer_layout.setSpacing(14)

        self._item_count_label = QLabel("0 items")
        self._item_count_label.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        footer_layout.addWidget(self._item_count_label)

        total_row = QHBoxLayout()
        total_caption = QLabel("Total")
        total_caption.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {p['text_primary']};")
        self._total_label = QLabel("$0.00")
        self._total_label.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 40px; color: {p['text_primary']};")
        total_row.addWidget(total_caption)
        total_row.addStretch(1)
        total_row.addWidget(self._total_label)
        footer_layout.addLayout(total_row)

        buttons_row = QHBoxLayout()
        card_button = ActionButton("Card", variant="accent")
        card_button.clicked.connect(lambda: self._checkout("Card"))
        cash_button = ActionButton("Cash", variant="dark")
        cash_button.clicked.connect(lambda: self._checkout("Cash"))
        buttons_row.addWidget(card_button)
        buttons_row.addWidget(cash_button)
        footer_layout.addLayout(buttons_row)

        layout.addWidget(footer)
        return panel

    def _render_cart(self) -> None:
        while self._cart_rows_layout.count() > 1:  # keep the trailing stretch
            item = self._cart_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        p = ORGANIC_PALETTE
        if not self._cart:
            empty = QLabel("Tap a product to add it")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                f"background-color: {p['surface']}; color: {p['text_secondary']}; "
                f"border-radius: {p['radius_lg']}; padding: 32px 24px; font-size: 16px;"
            )
            self._cart_rows_layout.insertWidget(0, empty)
        else:
            for index, line in enumerate(self._cart.values()):
                self._cart_rows_layout.insertWidget(index, self._build_cart_row(line))

        item_count = sum(line.quantity for line in self._cart.values())
        total = round(sum(line.line_total for line in self._cart.values()), 2)
        self._item_count_label.setText(f"{item_count} item{'s' if item_count != 1 else ''}")
        self._total_label.setText(f"${total:,.2f}")

    def _build_cart_row(self, line: _CartLine) -> QWidget:
        p = ORGANIC_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(12)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_label = QLabel(line.name)
        name_label.setStyleSheet(f"font-weight: 700; font-size: 16px; color: {p['text_primary']};")
        unit_label = QLabel(f"${line.unit_price:,.2f} each")
        unit_label.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']};")
        names.addWidget(name_label)
        names.addWidget(unit_label)
        names_widget = QWidget()
        names_widget.setLayout(names)
        layout.addWidget(names_widget, stretch=1)

        stepper = QHBoxLayout()
        stepper.setSpacing(4)
        dec_button = QPushButton("−")
        inc_button = QPushButton("+")
        for button in (dec_button, inc_button):
            button.setFixedSize(40, 40)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                f"border: none; border-radius: 20px; background-color: {p['background']}; "
                f"font-size: 20px; font-weight: 700; color: {p['text_primary']};"
            )
        dec_button.clicked.connect(lambda: self._change_quantity(line.barcode, -1))
        inc_button.clicked.connect(lambda: self._change_quantity(line.barcode, 1))
        qty_label = QLabel(str(line.quantity))
        qty_label.setAlignment(Qt.AlignCenter)
        qty_label.setFixedWidth(28)
        qty_label.setStyleSheet(f"font-weight: 700; font-size: 17px; color: {p['text_primary']};")
        stepper.addWidget(dec_button)
        stepper.addWidget(qty_label)
        stepper.addWidget(inc_button)
        stepper_widget = QWidget()
        stepper_widget.setLayout(stepper)
        stepper_widget.setStyleSheet(f"background-color: {p['surface']}; border-radius: 999px;")
        layout.addWidget(stepper_widget)

        line_total_label = QLabel(f"${line.line_total:,.2f}")
        line_total_label.setFixedWidth(70)
        line_total_label.setAlignment(Qt.AlignRight)
        line_total_label.setStyleSheet(f"font-weight: 700; font-size: 16px; color: {p['text_primary']};")
        layout.addWidget(line_total_label)

        return row

    # --- Cart mutation ---------------------------------------------------

    def _add_to_cart(self, product: Product) -> None:
        existing = self._cart.get(product.barcode)
        if existing is not None:
            existing.quantity = min(existing.quantity + 1, product.stock_quantity)
        else:
            self._cart[product.barcode] = _CartLine(
                barcode=product.barcode,
                name=product.name,
                unit_price=product.price,
                quantity=1,
                max_quantity=product.stock_quantity,
            )
        self._render_cart()

    def _change_quantity(self, barcode: str, delta: int) -> None:
        line = self._cart.get(barcode)
        if line is None:
            return
        new_quantity = min(line.quantity + delta, line.max_quantity)
        if new_quantity <= 0:
            del self._cart[barcode]
        else:
            line.quantity = new_quantity
        self._render_cart()

    def clear_cart(self) -> None:
        """Empty the current sale (a new cashier takes over the till)."""
        self._clear_cart()

    def _clear_cart(self) -> None:
        self._cart.clear()
        self._render_cart()

    # --- Data + checkout -------------------------------------------------

    def reload(self) -> None:
        try:
            self._all_products = stock_repository.products_at(self._location)
        except DataAccessError:
            self._all_products = []
        self._render_grid()
        self._render_cart()

    def _checkout(self, payment_label: str) -> None:
        if not self._cart:
            QMessageBox.information(self, "Cart is empty", "Add a product before completing a sale.")
            return

        pending = Transaction(
            items=[
                LineItem(
                    product_barcode=line.barcode,
                    product_name_at_sale=line.name,
                    unit_price_at_sale=line.unit_price,
                    quantity=line.quantity,
                )
                for line in self._cart.values()
            ]
        )

        try:
            finalized = complete_sale(pending, self._location, current_session.actor())
        except InsufficientStockError as exc:
            QMessageBox.warning(
                self,
                "Not enough stock",
                f"Only {exc.available} of {exc.barcode!r} left on this shelf - someone else may have just sold it. "
                "Adjust the cart and try again.",
            )
            self.reload()
            return
        except DataAccessError as exc:
            QMessageBox.warning(self, "Sale failed", str(exc))
            return

        self._cart.clear()
        self.reload()
        QMessageBox.information(
            self,
            "Sale complete",
            f"{payment_label} sale #{finalized.id} - total ${finalized.total:,.2f}",
        )
