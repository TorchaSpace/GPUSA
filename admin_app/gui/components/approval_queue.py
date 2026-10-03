"""The admin mockup's "Admin action required · Out-of-range purchase
requests" list (Inventory Dashboard.dc.html's approvals dropdown): one
card per held purchase order with Approve / Reject, a "<total> held"
figure in the header, an "All requests reviewed." empty state, and a
last-action line in the footer.

Recreated from the mockup, with its fields mapped onto what's real:
- "PR-2291 · WH North · 14 min"  -> the order's PO number · site · age
- "40 × Final drive assembly"   -> quantity × product (barcode)
- "Requested by M. Okafor"      -> the supplier and unit price instead:
  there's no login system, so who pressed Submit isn't known, and a
  made-up name would be worse than none.
- "$186,400 · limit $120,000 · +55% over" -> the order total, the
  product's safe unit-price band, and how far the unit price is above /
  below it (or "no band set"). The mockup's limit was a per-site spend
  limit; this app's rule is a per-product unit-price band (see
  database/purchase_order_repository.py), so that's what's shown.
- "Review details" is dropped - the full order is already on the card.

Emits approve_requested(order_id) / reject_requested(order_id); the host
page does the database call, so this widget stays presentation-only.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from admin_app.gui.components.compact_button import CompactButton
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING
from shared.formatting import age_text, format_amount
from shared.models import PurchaseOrder


def deviation_text(order: PurchaseOrder) -> str:
    """"+9% over" or "12% under" for a held order ("" when there's no
    band to measure against - band_text() already says so)."""
    if order.range_min is None or order.range_max is None:
        return ""
    if order.unit_price > order.range_max and order.range_max > 0:
        return f"+{round((order.unit_price / order.range_max - 1) * 100)}% over"
    if order.unit_price < order.range_min and order.range_min > 0:
        return f"{round((1 - order.unit_price / order.range_min) * 100)}% under"
    return "within band"


def band_text(order: PurchaseOrder) -> str:
    if order.range_min is None or order.range_max is None:
        return "no safe band set for this product"
    return f"band {format_amount(order.range_min)}–{format_amount(order.range_max)} / unit"


class _PrimaryButton(QPushButton):
    """The mockup's gold `.btn-primary` - Approve is the one filled action."""

    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        p = CLASSICAL_PALETTE
        self.setMaximumHeight(28)
        self.setStyleSheet(
            f"""
            QPushButton {{ background-color: {p['accent']}; color: #161514; border: 1px solid {p['accent']};
                border-radius: {p['radius_sm']}; padding: 2px 14px; font-size: 13px; }}
            QPushButton:hover {{ background-color: #ecc187; }}
            """
        )


class ApprovalCard(QFrame):
    approve_clicked = Signal(int)
    reject_clicked = Signal(int)

    def __init__(self, order: PurchaseOrder, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.order = order
        self.setObjectName("approvalCard")
        self.setStyleSheet(
            f"#approvalCard {{ border: none; border-bottom: 1px solid {p['border']}; }} QLabel {{ border: none; }}"
        )
        grid = QGridLayout(self)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)

        left = QVBoxLayout()
        left.setSpacing(2)
        meta = QLabel(
            f"<span style='color:{p['accent']}'>{order.number}</span>"
            f" · {order.site} · {age_text(order.created_at)}"
        )
        meta.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        left.addWidget(meta)
        item = QLabel(f"{order.quantity} × {order.product_name}  ({order.product_barcode})")
        item.setWordWrap(True)
        item.setMinimumHeight(20)
        item.setStyleSheet(f"font-size: 14px; color: {p['text_primary']};")
        left.addWidget(item)
        detail = QLabel(f"{format_amount(order.unit_price)} / unit from {order.supplier}")
        detail.setWordWrap(True)
        detail.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        left.addWidget(detail)
        grid.addLayout(left, 0, 0)

        right = QVBoxLayout()
        right.setSpacing(0)
        amount = QLabel(format_amount(order.total))
        amount.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 20px; color: {p['text_primary']};")
        band = QLabel(band_text(order))
        band.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        over = QLabel(deviation_text(order))
        over.setStyleSheet(f"font-size: 11px; color: {p['accent']};")
        over.setVisible(bool(over.text()))
        for widget, min_height in ((amount, 30), (band, 16), (over, 16)):
            widget.setAlignment(Qt.AlignRight)
            # Explicit minimums: a stylesheet font-size isn't always
            # reflected in a QLabel's size hint before first polish, which
            # let the larger amount label get squeezed under the band line.
            widget.setMinimumHeight(min_height)
            right.addWidget(widget)
        grid.addLayout(right, 0, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.approve_button = _PrimaryButton("Approve")
        self.approve_button.clicked.connect(lambda: self.approve_clicked.emit(order.id))
        self.reject_button = CompactButton("Reject")
        self.reject_button.clicked.connect(lambda: self.reject_clicked.emit(order.id))
        buttons.addWidget(self.approve_button)
        buttons.addWidget(self.reject_button)
        buttons.addStretch(1)
        grid.addLayout(buttons, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)


class ApprovalQueue(QWidget):
    """The card list + empty state + footer line. Call set_orders() with
    the current pending orders; set_last_action() for the footer."""

    approve_requested = Signal(int)
    reject_requested = Signal(int)

    DEFAULT_FOOTER = "Orders priced outside a product's safe band are held for admin sign-off."

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._cards_layout = QVBoxLayout()
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(0)
        outer.addLayout(self._cards_layout)

        self._empty_label = QLabel("All requests reviewed.")
        self._empty_label.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-size: 18px; color: {p['text_secondary']}; "
            f"padding: 28px 16px; border: none;"
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self._empty_label)

        self._footer = QLabel(self.DEFAULT_FOOTER)
        self._footer.setWordWrap(True)
        self._footer.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']}; padding: 8px 16px; border: none;")
        outer.addWidget(self._footer)

        self._cards: list[ApprovalCard] = []

    def set_orders(self, orders: list[PurchaseOrder]) -> None:
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            # Hide first: deleteLater() only runs once control returns to
            # the event loop, and until then the old card would still be
            # painted underneath its replacement (overlapping text).
            card.hide()
            card.deleteLater()
        self._cards = []
        for order in orders:
            card = ApprovalCard(order)
            card.approve_clicked.connect(self.approve_requested.emit)
            card.reject_clicked.connect(self.reject_requested.emit)
            self._cards_layout.addWidget(card)
            self._cards.append(card)
        self._empty_label.setVisible(not orders)

    def cards(self) -> list[ApprovalCard]:
        return list(self._cards)

    def set_last_action(self, text: str) -> None:
        self._footer.setText(text)

    def last_action(self) -> str:
        return self._footer.text()
