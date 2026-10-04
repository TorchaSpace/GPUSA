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

from database import account_repository, product_repository, stock_repository
from database.exceptions import (
    DATABASE_ERRORS,
    DealershipInactiveError,
    InsufficientStockError,
    PriceChangedError,
    ProductNotFoundError,
    SessionInvalidError,
)
from pos_app.gui.auth_flow import till_dealership_problem
from pos_app.gui.components.action_button import ActionButton
from pos_app.gui.product_status import stock_status
from pos_app.services.checkout_service import complete_sale
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.models import UNASSIGNED, LineItem, Product, StockLocation, Transaction
from shared import current_session
from shared.warehousing import tr_or

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
            f"border: none; background: transparent; font-family: {p['font_family_css']}; font-size: 19px; color: {p['text_primary']};"
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
        # Deactivated products can't be sold (and the search matches a scanned barcode as well as names).
        visible = [
            product for product in self._all_products
            if product.is_active and (query in product.name.lower() or query in product.barcode.lower())
        ]

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
            f"font-family: {FONT_HEADING_CSS}; font-size: 24px;"
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
        title.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 28px; color: {p['text_primary']};")
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

        # Why the cart changed under the cashier (a price moved, an item was withdrawn, stock ran short).
        self._cart_notice = QLabel()
        self._cart_notice.setWordWrap(True)
        self._cart_notice.setStyleSheet(
            "background-color: #fff6d6; color: #3a2a05; border: 1px solid #f4b400; "
            "border-radius: 12px; padding: 8px 12px; margin: 0 16px 8px 16px; font-size: 14px;"
        )
        self._cart_notice.hide()
        layout.addWidget(self._cart_notice)

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
        self._total_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 40px; color: {p['text_primary']};")
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
        except DATABASE_ERRORS:
            self._all_products = []
        issues = self._refresh_cart()  # prices / availability may have changed since the lines were added
        self._show_notice(issues)
        self._render_grid()
        self._render_cart()

    # --- keeping the cart true to the database -------------------------------

    def _cart_triples(self) -> list[tuple[str, float, int]]:
        return [(line.barcode, line.unit_price, line.quantity) for line in self._cart.values()]

    def _refresh_cart(self) -> list:
        """Re-read every cart line from the database and bring it in line:
        a changed price is taken over, a withdrawn / deactivated product is
        removed, a quantity above what the shelf holds is cut back.
        Returns the CartIssues found (empty: the cart was already right)."""
        if not self._cart:
            return []
        try:
            issues = product_repository.check_cart(self._location, self._cart_triples())
        except DATABASE_ERRORS:
            return []
        self._apply_issues(issues)
        return issues

    def _apply_issues(self, issues) -> None:
        for issue in issues:
            line = self._cart.get(issue.barcode)
            if line is None:
                continue
            if issue.kind == "unavailable":
                del self._cart[issue.barcode]
            elif issue.kind == "price":
                line.unit_price = issue.current_price
                line.max_quantity = issue.available
                line.quantity = min(line.quantity, issue.available)
            elif issue.kind == "stock":
                line.max_quantity = issue.available
                line.quantity = min(line.quantity, issue.available)
            if issue.barcode in self._cart and self._cart[issue.barcode].quantity <= 0:
                del self._cart[issue.barcode]
        # lines that were fine keep their limit current too
        for product in self._all_products:
            line = self._cart.get(product.barcode)
            if line is not None:
                line.max_quantity = product.stock_quantity

    def _show_notice(self, issues) -> None:
        if issues:
            self._cart_notice.setText(" ".join(issue.message for issue in issues)
                                      + " " + tr_or("pos.cart_refreshed", "The cart has been updated - check it before charging."))
            self._cart_notice.show()
        else:
            self._cart_notice.hide()

    def cart_notice(self) -> str:
        """The visible cart-changed notice ('' when none) - for tests."""
        return self._cart_notice.text() if not self._cart_notice.isHidden() else ""

    def _warn_sign_in_again(self) -> None:
        QMessageBox.warning(
            self, tr_or("pos.sign_in_again_title", "Sign in again"),
            str(SessionInvalidError()) + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
        )

    def _cart_changed_by_database(self, exc: Exception) -> None:
        """finalize_transaction() refused the sale because the cart no longer
        matches the database. Re-read it (new prices, withdrawn lines
        removed, quantities cut back), show the 'cart changed' notice and
        tell the cashier nothing was charged."""
        self.reload()  # refreshes the cart from the database and shows what changed
        if not self.cart_notice():  # the refresh found nothing to add: use the database's own words
            self._cart_notice.setText(str(exc))
            self._cart_notice.show()
        QMessageBox.warning(
            self,
            tr_or("pos.cart_changed_title", "Cart changed"),
            str(exc) + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
        )

    def _checkout(self, payment_label: str) -> None:
        if not self._cart:
            QMessageBox.information(self, "Cart is empty", "Add a product before completing a sale.")
            return

        # Re-read prices and availability NOW: the till may have been open for a while, and an admin
        # can change a price (or withdraw a product) at any time. Refuse, refresh, and let the cashier look again.
        try:
            issues = product_repository.check_cart(self._location, self._cart_triples())
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, "Sale failed", str(exc))
            return
        if issues:
            self._apply_issues(issues)
            self.reload()  # fresh grid + cart (clears the notice, so show it after)
            self._show_notice(issues)
            QMessageBox.warning(
                self,
                tr_or("pos.cart_changed_title", "Cart changed"),
                " ".join(issue.message for issue in issues)
                + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
            )
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

        # Friendly pre-checks. finalize_transaction() repeats every one of them
        # inside its own transaction (that is the authoritative check - these
        # only spare the cashier a round trip), so nothing here is load-bearing.
        session = current_session.get()
        if session is not None and not account_repository.is_session_valid(session):
            self._warn_sign_in_again()
            return
        if self._location.kind == "dealership":
            problem = till_dealership_problem(self._location.code)
            if problem:
                QMessageBox.warning(self, tr_or("pos.till_off_title", "Till switched off"), problem)
                return

        try:
            finalized = complete_sale(pending, self._location, current_session.actor())
        except (PriceChangedError, ProductNotFoundError, InsufficientStockError) as exc:
            # The database found the cart out of date at the moment of charging
            # (a price moved, a product was withdrawn/deactivated, stock ran
            # out): nothing was charged. Bring the cart in line and say so.
            self._cart_changed_by_database(exc)
            return
        except SessionInvalidError:
            self._warn_sign_in_again()
            return
        except DealershipInactiveError as exc:
            QMessageBox.warning(self, tr_or("pos.till_off_title", "Till switched off"), str(exc))
            return
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, "Sale failed", str(exc))
            return

        self._cart.clear()
        self._cart_notice.hide()
        self.reload()
        QMessageBox.information(
            self,
            "Sale complete",
            f"{payment_label} sale #{finalized.id} - total ${finalized.total:,.2f}",
        )
