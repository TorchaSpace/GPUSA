"""Purchasing Operations - the real build of the Warehouse Console
mockup's Manager Portal "01 Purchasing Operations" tab (Warehouse
Console.dc.html, portalTab 'purch'), replacing its themed placeholder.

Recreated from the mockup, field for field:
- "New purchase · WH-01" form: Item, Supplier (pre-filled from the
  item's default supplier, editable), Quantity, Unit price, Order total.
- "Safe price range · set by Admin" (read-only): min/max per unit, the
  approved-band bar with the typed price's marker, and a live verdict -
  within range / out of safe range (above the ceiling) / below safe
  range - that switches the submit button between "Send purchase order"
  and "Submit for admin approval".
- After submitting: an "Awaiting Admin Approval" banner (held orders) or
  a "PO-xxxxx sent" confirmation, each with "New order".
- "Purchase orders · <site>" table with each order's status.

Real, not a placeholder: everything goes through
database.purchase_order_repository - the send-vs-hold decision is made
there, authoritatively, with the same shared.models.hold_reason_for()
rule this form previews with, so the two can't disagree. The safe band
comes from what an administrator set on admin_app's Purchase Requests
page.

Differences from the mockup, deliberately:
- No "Simulate admin approval" button - that was the mockup's demo
  stand-in for the admin side, which now really exists (admin_app >
  Purchase Requests). Instead this panel re-checks its orders every few
  seconds, so the banner flips to "sent" or "rejected" on its own once
  an administrator decides.
- A product with no band set yet shows "No safe range set" and is held
  for approval (the mockup never shows that case - every mockup SKU has
  a band).
- No "Based on the last 90 days of supplier quotes" line under the band:
  nothing computes the band from quotes; an administrator types it.
- Amounts are shown without a currency symbol, like everywhere else in
  the app.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import depot_app.gui.icons as icons
from database import product_repository, purchase_order_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.formatting import format_amount, local_datetime_text, local_time_text, parse_amount, round_money
from shared.gui_kit.icon_kit import svg_to_icon
from shared.gui_kit.polling import PollingTimer
from shared.models import PriceRange, Product, PurchaseOrder, hold_reason_for
from shared import current_session

MAX_QUANTITY = purchase_order_repository.MAX_QUANTITY  # same cap the repository enforces
_MAX_QUANTITY_DIGITS = len(str(MAX_QUANTITY))
STATUS_POLL_INTERVAL_MS = 5000
_TABLE_LIMIT = 20

_STATUS_TEXT = {
    "pending": "Awaiting Admin Approval",
    "sent": "Sent",
    "rejected": "Rejected",
}


def _order_total(price: float, quantity: int) -> float:
    """price x quantity, half-up to cents - the same arithmetic as
    PurchaseOrder.total, so the preview equals the stored order."""
    return PurchaseOrder("", "", "", quantity, price, "", "pending").total


def parse_quantity(text: str) -> int | None:
    """A typed quantity: ASCII digits only (str.isdigit() alone accepts
    "²" and Arabic-Indic digits that int() then rejects or misreads),
    bounded length, 1..MAX_QUANTITY. None for anything else."""
    text = (text or "").strip()
    if not text or len(text) > _MAX_QUANTITY_DIGITS + 4:  # tolerate leading zeros, not megabyte pastes
        return None
    if not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    return value if 1 <= value <= MAX_QUANTITY else None


def decision_tooltip(order: PurchaseOrder) -> str:
    """Who decided, when, and why - what an admin's note says, kept
    visible on the order after the banner is gone. "" if undecided."""
    lines = []
    if order.decided_at:
        who = f" by {order.decided_by}" if order.decided_by else ""
        verb = "Rejected" if order.status == "rejected" else "Approved"
        lines.append(f"{verb} {local_datetime_text(order.decided_at)}{who}")
    if order.decision_note:
        lines.append(f"Note: {order.decision_note}")
    return "\n".join(lines)


def status_text(order: PurchaseOrder) -> str:
    if order.was_approved:
        return "Approved · Sent"
    return _STATUS_TEXT.get(order.status, order.status)


# --- Small painted pieces ------------------------------------------------


class HazardStripe(QWidget):
    """The mockup's diagonal black/white "hazard" stripe that edges the
    Awaiting Approval banner and the out-of-range warning box."""

    def __init__(self, dark: str, light: str, width: int = 12, parent: QWidget | None = None):
        super().__init__(parent)
        self._dark, self._light = QColor(dark), QColor(light)
        self.setFixedWidth(width)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._light)
        pen = QPen(self._dark)
        pen.setWidth(4)
        painter.setPen(pen)
        painter.setClipRect(self.rect())
        height = self.height()
        for y in range(-self.width(), height + self.width(), 10):
            painter.drawLine(QPointF(0, y + self.width()), QPointF(self.width(), y))


class PriceBandBar(QWidget):
    """The mockup's "Approved band" bar: a track from 0 to a scale max, the
    safe band filled in accent blue, the region above the ceiling hatched,
    and a marker + label at the typed unit price."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._range: PriceRange | None = None
        self._price: float | None = None
        self.setMinimumHeight(64)

    def set_state(self, price_range: PriceRange | None, price: float | None) -> None:
        self._range, self._price = price_range, price
        self.update()

    def scale_max(self) -> float:
        top = self._range.max_unit_price if self._range else 0
        candidates = [top * 1.25, (self._price or 0) * 1.1, 1.0]
        return max(candidates)

    def paintEvent(self, event) -> None:
        p = INDUSTRY_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        track = QRectF(8, 30, self.width() - 16, 14)
        painter.fillRect(track, QColor("#e3e3e6"))
        painter.setPen(QPen(QColor("#9d9da1")))
        painter.drawRect(track)

        if self._range is None:
            painter.setPen(QPen(QColor(p["text_secondary"])))
            painter.drawText(track, Qt.AlignCenter, "No safe range set")
            return

        scale = self.scale_max()

        def x_at(value: float) -> float:
            return track.left() + track.width() * min(max(value / scale, 0.0), 1.0)

        lo, hi = x_at(self._range.min_unit_price), x_at(self._range.max_unit_price)
        painter.fillRect(QRectF(lo, track.top(), max(hi - lo, 2), track.height()), QColor(p["accent"]))

        hatch_pen = QPen(QColor("#8a8a8e"))
        hatch_pen.setWidth(1)
        painter.setPen(hatch_pen)
        painter.setClipRect(QRectF(hi, track.top(), track.right() - hi, track.height()))
        x = hi - track.height()
        while x < track.right():
            painter.drawLine(QPointF(x, track.bottom()), QPointF(x + track.height(), track.top()))
            x += 6
        painter.setClipping(False)

        if self._price and self._price > 0:
            mx = x_at(self._price)
            marker_pen = QPen(QColor(p["text_primary"]))
            marker_pen.setWidth(2)
            painter.setPen(marker_pen)
            painter.drawLine(QPointF(mx, track.top() - 6), QPointF(mx, track.bottom() + 8))
            text = format_amount(self._price)
            font = QFont(self.font())
            font.setBold(True)
            painter.setFont(font)
            width = painter.fontMetrics().horizontalAdvance(text) + 12
            left = min(max(mx - width / 2, 0), self.width() - width)
            box = QRectF(left, 4, width, 20)
            painter.fillRect(box, QColor(p["text_primary"]))
            painter.setPen(QPen(QColor(p["background"])))
            painter.drawText(box, Qt.AlignCenter, text)


# --- The panel -----------------------------------------------------------


def _kicker(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(
        f"font-size: 12px; letter-spacing: 1px; color: {INDUSTRY_PALETTE['text_secondary']}; border: none;"
    )
    return label


def _input_style(font_px: int) -> str:
    p = INDUSTRY_PALETTE
    return (
        f"background-color: {p['surface_raised']}; color: {p['text_primary']}; "
        f"border: 1px solid {p['border']}; border-radius: 0; padding: 8px 10px; font-size: {font_px}px;"
    )


class PurchasingPanel(QWidget):
    """Raise purchase orders for `site` and watch their status."""

    def __init__(self, site: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._site = site
        self._products: list[Product] = []
        self._ranges: dict[str, PriceRange] = {}
        self._last_order: PurchaseOrder | None = None
        self._last_default_supplier = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(18)

        self._awaiting_banner = self._build_awaiting_banner()
        self._sent_banner = self._build_notice_banner()
        outer.addWidget(self._awaiting_banner)
        outer.addWidget(self._sent_banner)

        self._form = self._build_form()
        outer.addWidget(self._form)

        outer.addWidget(self._build_orders_table())

        self._poller = PollingTimer(self._fetch_orders, interval_ms=STATUS_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_orders_fetched)

        self.reload_catalog()
        self._show_form()
        self._refresh_orders_now()

    # --- lifecycle ---------------------------------------------------------

    def start_polling(self) -> None:
        self._poller.start()

    def stop_polling(self) -> None:
        self._poller.stop()

    # --- banners -------------------------------------------------------

    def _build_awaiting_banner(self) -> QWidget:
        p = INDUSTRY_PALETTE
        banner = QWidget()
        banner.setAttribute(Qt.WA_StyledBackground, True)
        banner.setStyleSheet(f"background-color: {p['text_primary']};")
        row = QHBoxLayout(banner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(HazardStripe(p["text_primary"], p["background"], width=16))

        body = QVBoxLayout()
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(14)

        title_row = QHBoxLayout()
        title = QLabel("AWAITING ADMIN APPROVAL")
        title.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; "
            f"letter-spacing: 1px; color: {p['background']};"
        )
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._awaiting_number = QLabel()
        self._awaiting_number.setStyleSheet(f"font-weight: 700; font-size: 18px; color: {p['background']};")
        title_row.addWidget(self._awaiting_number)
        body.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        self._awaiting_values: dict[str, QLabel] = {}
        for column, key in enumerate(("Item", "Unit price", "Safe band", "Order total")):
            caption = QLabel(key.upper())
            caption.setStyleSheet("font-size: 11px; letter-spacing: 1px; color: #b9b9bd;")
            value = QLabel()
            value.setTextFormat(Qt.PlainText)
            value.setStyleSheet(f"font-weight: 700; font-size: 14px; color: {p['background']};")
            grid.addWidget(caption, 0, column)
            grid.addWidget(value, 1, column)
            self._awaiting_values[key] = value
        body.addLayout(grid)

        self._awaiting_message = QLabel()
        self._awaiting_message.setTextFormat(Qt.PlainText)
        self._awaiting_message.setWordWrap(True)
        self._awaiting_message.setStyleSheet(f"font-size: 14px; color: {p['background']};")
        body.addWidget(self._awaiting_message)

        new_order = IndustryButton("New order", variant="ghost")
        new_order.setStyleSheet(
            new_order.styleSheet()
            + f"QPushButton {{ background-color: {p['background']}; color: {p['text_primary']}; }}"
        )
        new_order.clicked.connect(self._show_form)
        button_row = QHBoxLayout()
        button_row.addWidget(new_order)
        button_row.addStretch(1)
        body.addLayout(button_row)

        row.addLayout(body, stretch=1)
        return banner

    def _build_notice_banner(self) -> QWidget:
        banner = QWidget()
        banner.setAttribute(Qt.WA_StyledBackground, True)
        banner.setObjectName("noticeBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)
        self._notice_icon = QLabel()
        row.addWidget(self._notice_icon)
        self._notice_text = QLabel()
        self._notice_text.setTextFormat(Qt.RichText)  # callers html.escape every value they put in
        self._notice_text.setWordWrap(True)
        row.addWidget(self._notice_text, stretch=1)
        new_order = IndustryButton("New order", variant="ghost")
        new_order.clicked.connect(self._show_form)
        row.addWidget(new_order)
        return banner

    def _set_notice(self, text: str, positive: bool) -> None:
        p = INDUSTRY_PALETTE
        if positive:
            self._sent_banner.setStyleSheet(
                f"#noticeBanner {{ background-color: {p['accent_100']}; border: 1.5px solid {p['accent']}; }}"
            )
            self._notice_icon.setPixmap(svg_to_icon(icons.CHECKMARK, p["accent"], size=24).pixmap(24, 24))
            self._notice_text.setStyleSheet(f"font-size: 14px; color: {p['accent_900']}; border: none;")
        else:
            self._sent_banner.setStyleSheet(
                f"#noticeBanner {{ background-color: #fff6d6; border: 2px solid {p['text_primary']}; }}"
            )
            self._notice_icon.clear()
            self._notice_text.setStyleSheet(f"font-size: 14px; color: {p['text_primary']}; border: none;")
        self._notice_text.setText(text)

    # --- form ----------------------------------------------------------

    def _build_form(self) -> QWidget:
        p = INDUSTRY_PALETTE
        form = BlueprintFrame(tick_color=p["text_primary"])
        form.setStyleSheet(f"BlueprintFrame {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        columns = QHBoxLayout(form)
        columns.setContentsMargins(20, 18, 20, 20)
        columns.setSpacing(28)

        # Left column: the order itself.
        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(_kicker(f"New purchase · {self._site}"))

        left.addWidget(_kicker("Item"))
        self._item_input = QComboBox()
        self._item_input.setStyleSheet(f"QComboBox {{ {_input_style(16)} min-height: 34px; }}")
        self._item_input.currentIndexChanged.connect(self._on_item_changed)
        left.addWidget(self._item_input)

        left.addWidget(_kicker("Supplier"))
        self._supplier_input = QLineEdit()
        self._supplier_input.setStyleSheet(_input_style(16))
        left.addWidget(self._supplier_input)

        numbers = QGridLayout()
        numbers.setHorizontalSpacing(12)
        numbers.addWidget(_kicker("Quantity"), 0, 0)
        numbers.addWidget(_kicker("Unit price"), 0, 1)
        self._qty_input = QLineEdit()
        self._qty_input.setPlaceholderText("0")
        self._qty_input.setStyleSheet(_input_style(24))
        self._qty_input.textChanged.connect(self._update_preview)
        self._price_input = QLineEdit()
        self._price_input.setPlaceholderText("0,00")
        self._price_input.setStyleSheet(_input_style(24))
        self._price_input.textChanged.connect(self._update_preview)
        numbers.addWidget(self._qty_input, 1, 0)
        numbers.addWidget(self._price_input, 1, 1)
        left.addLayout(numbers)

        total_row = QHBoxLayout()
        total_row.addWidget(_kicker("Order total"))
        total_row.addStretch(1)
        self._total_label = QLabel("0.00")
        self._total_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 32px; color: {p['text_primary']}; border: none;"
        )
        total_row.addWidget(self._total_label)
        left.addLayout(total_row)
        left.addStretch(1)
        columns.addLayout(left, stretch=1)

        # Right column: the admin-set band and the verdict.
        right = QVBoxLayout()
        right.setSpacing(10)
        band_header = QHBoxLayout()
        lock = QLabel()
        lock.setPixmap(svg_to_icon(icons.LOCK, p["text_secondary"], size=16).pixmap(16, 16))
        lock.setStyleSheet("border: none;")
        band_header.addWidget(lock)
        band_header.addWidget(_kicker("Safe price range · set by Admin"), stretch=1)
        read_only = QLabel("READ-ONLY")
        read_only.setStyleSheet(
            f"font-size: 10px; letter-spacing: 1px; color: {p['text_secondary']}; "
            f"border: 1px solid {p['text_secondary']}; padding: 1px 6px;"
        )
        band_header.addWidget(read_only)
        right.addLayout(band_header)

        band_values = QHBoxLayout()
        self._band_min = QLabel("—")
        self._band_max = QLabel("—")
        for label in (self._band_min, self._band_max):
            label.setStyleSheet(
                f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 24px; color: {p['text_primary']}; border: none;"
            )
        per_unit = QLabel("per unit")
        per_unit.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']}; border: none;")
        band_values.addWidget(self._band_min)
        band_values.addStretch(1)
        band_values.addWidget(per_unit)
        band_values.addStretch(1)
        band_values.addWidget(self._band_max)
        right.addLayout(band_values)

        self._band_bar = PriceBandBar()
        right.addWidget(self._band_bar)
        scale_row = QHBoxLayout()
        zero = QLabel("0")
        approved = QLabel("Approved band")
        self._scale_label = QLabel("")
        for label in (zero, approved, self._scale_label):
            label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']}; border: none;")
        scale_row.addWidget(zero)
        scale_row.addStretch(1)
        scale_row.addWidget(approved)
        scale_row.addStretch(1)
        scale_row.addWidget(self._scale_label)
        right.addLayout(scale_row)

        only_admin = QLabel("Only an administrator can change this range (Admin > Purchase requests).")
        only_admin.setWordWrap(True)
        only_admin.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; border: none;")
        right.addWidget(only_admin)

        self._verdict_box = QWidget()
        self._verdict_box.setAttribute(Qt.WA_StyledBackground, True)
        self._verdict_box.setObjectName("verdictBox")
        verdict_row = QHBoxLayout(self._verdict_box)
        verdict_row.setContentsMargins(0, 0, 0, 0)
        verdict_row.setSpacing(0)
        self._verdict_stripe = HazardStripe(p["text_primary"], p["background"], width=10)
        verdict_row.addWidget(self._verdict_stripe)
        verdict_text = QVBoxLayout()
        verdict_text.setContentsMargins(12, 10, 12, 10)
        verdict_text.setSpacing(2)
        self._verdict_title = QLabel()
        self._verdict_title.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 20px; color: {p['text_primary']}; border: none;"
        )
        self._verdict_body = QLabel()
        self._verdict_body.setTextFormat(Qt.PlainText)
        self._verdict_body.setWordWrap(True)
        verdict_text.addWidget(self._verdict_title)
        verdict_text.addWidget(self._verdict_body)
        verdict_row.addLayout(verdict_text, stretch=1)
        right.addWidget(self._verdict_box)

        self._error_label = QLabel()
        self._error_label.setTextFormat(Qt.PlainText)
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color: {p['text_primary']}; background-color: #fff6d6; border: 1px solid #f4b400; "
            f"padding: 6px 10px; font-size: 12px;"
        )
        self._error_label.hide()
        right.addWidget(self._error_label)

        self._send_button = IndustryButton("Send purchase order", variant="accent")
        self._send_button.setMinimumHeight(54)
        self._send_button.clicked.connect(self._submit)
        self._hold_button = IndustryButton("Submit for admin approval", variant="primary")
        self._hold_button.setMinimumHeight(54)
        self._hold_button.clicked.connect(self._submit)
        right.addWidget(self._send_button)
        right.addWidget(self._hold_button)
        right.addStretch(1)
        columns.addLayout(right, stretch=1)
        return form

    # --- orders table ------------------------------------------------------

    def _build_orders_table(self) -> QWidget:
        p = INDUSTRY_PALETTE
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_kicker(f"Purchase orders · {self._site}"))
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            ["PO", "Time", "Item", "Supplier", "Qty", "Unit", "Total", "Status", "Admin note"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self._table.setMinimumHeight(220)
        self._table.setStyleSheet(
            f"""
            QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; gridline-color: {p['border']}; font-size: 13px; }}
            QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
                border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
            """
        )
        layout.addWidget(self._table)
        return box

    # --- data ----------------------------------------------------------

    def reload_catalog(self) -> None:
        """Re-read products and bands (the band may have been changed in
        Admin since this dialog opened)."""
        try:
            self._products = product_repository.list_active()
            self._ranges = {r.product_barcode: r for r in purchase_order_repository.list_price_ranges()}
        except (ValueError, *DATABASE_ERRORS) as exc:
            # Keep the last good catalog (an empty one would claim "No
            # products yet") and say the refresh failed.
            self._show_error(f"Couldn't refresh products and price ranges: {exc}")

        current = self._item_input.currentData()
        self._item_input.blockSignals(True)
        self._item_input.clear()
        for product in self._products:
            self._item_input.addItem(f"{product.barcode} · {product.name}", product.barcode)
        if current is not None:
            index = self._item_input.findData(current)
            if index >= 0:
                self._item_input.setCurrentIndex(index)
        self._item_input.blockSignals(False)
        self._on_item_changed()

    def _selected_barcode(self) -> str | None:
        return self._item_input.currentData()

    def _selected_range(self) -> PriceRange | None:
        barcode = self._selected_barcode()
        return self._ranges.get(barcode) if barcode else None

    def _on_item_changed(self) -> None:
        price_range = self._selected_range()
        default_supplier = (price_range.default_supplier or "") if price_range else ""
        # Pre-fill like the mockup (SKUS[sku].supplier), but never throw
        # away something the manager typed themselves.
        current = self._supplier_input.text().strip()
        if not current or current == self._last_default_supplier:
            self._supplier_input.setText(default_supplier)
        self._last_default_supplier = default_supplier
        self._update_preview()

    def _typed_quantity(self) -> int | None:
        return parse_quantity(self._qty_input.text())

    def _typed_price(self) -> float | None:
        """The typed unit price, rounded half-up to cents - exactly what
        the repository will store and judge against the band. None if it
        isn't a usable price."""
        value = parse_amount(self._price_input.text())
        if value is None:
            return None
        try:
            return round_money(value, "Unit price")
        except ValueError:
            return None

    def _update_preview(self) -> None:
        price_range = self._selected_range()
        price = self._typed_price()
        quantity = self._typed_quantity()

        self._total_label.setText(format_amount(_order_total(price or 0, quantity or 0)))
        self._band_min.setText(format_amount(price_range.min_unit_price) if price_range else "—")
        self._band_max.setText(format_amount(price_range.max_unit_price) if price_range else "—")
        self._band_bar.set_state(price_range, price)
        self._scale_label.setText(format_amount(self._band_bar.scale_max()) if price_range else "")

        has_product = self._selected_barcode() is not None
        reason = hold_reason_for(price_range, price) if (price and price > 0) else None
        if not has_product:
            self._set_verdict("No products yet", "Add products in the Admin app first.", warning=True)
        elif price is None or price <= 0:
            if price_range is None:
                self._set_verdict(
                    "No safe range set",
                    "An administrator hasn't set a safe price range for this item yet, so any order "
                    "for it will be held until an administrator approves it.",
                    warning=True,
                )
            else:
                self._set_verdict("", "Enter a unit price to check it against the safe range.", warning=None)
        elif reason == "above_range":
            over = price - price_range.max_unit_price
            pct = round(over / price_range.max_unit_price * 100) if price_range.max_unit_price else 0
            self._set_verdict(
                "Out of safe range",
                f"{format_amount(price)} is {format_amount(over)} ({pct}%) above the "
                f"{format_amount(price_range.max_unit_price)} ceiling. This order will be held until an "
                f"administrator approves it.",
                warning=True,
            )
        elif reason == "below_range":
            self._set_verdict(
                "Below safe range",
                f"{format_amount(price)} is under the {format_amount(price_range.min_unit_price)} floor. "
                f"This order will be held until an administrator approves it.",
                warning=True,
            )
        elif reason == "no_range":
            self._set_verdict(
                "No safe range set",
                "An administrator hasn't set a safe price range for this item yet, so this order will "
                "be held until an administrator approves it.",
                warning=True,
            )
        else:
            self._set_verdict("", "Within safe range. This order can be sent directly.", warning=False)

        held = reason is not None or (has_product and price_range is None)
        self._send_button.setVisible(not held)
        self._hold_button.setVisible(held)
        enabled = has_product
        self._send_button.setEnabled(enabled)
        self._hold_button.setEnabled(enabled)

    def _set_verdict(self, title: str, body: str, warning: bool | None) -> None:
        p = INDUSTRY_PALETTE
        self._verdict_title.setText(title.upper())
        self._verdict_title.setVisible(bool(title))
        self._verdict_body.setText(body)
        self._verdict_stripe.setVisible(warning is True)
        if warning is True:
            self._verdict_box.setStyleSheet(f"#verdictBox {{ border: 2px solid {p['text_primary']}; }}")
            self._verdict_body.setStyleSheet(f"font-size: 13px; color: {p['text_primary']}; border: none;")
        elif warning is False:
            self._verdict_box.setStyleSheet(
                f"#verdictBox {{ border: 1.5px solid {p['accent']}; background-color: {p['accent_100']}; }}"
            )
            self._verdict_body.setStyleSheet(
                f"font-size: 13px; font-weight: 600; color: {p['accent_900']}; border: none;"
            )
        else:
            self._verdict_box.setStyleSheet("#verdictBox { border: none; }")
            self._verdict_body.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']}; border: none;")

    # --- actions ---------------------------------------------------------

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()

    def _submit(self) -> None:
        self._error_label.hide()
        barcode = self._selected_barcode()
        if barcode is None:
            self._show_error("Pick an item first.")
            return
        quantity = self._typed_quantity()
        if not quantity:
            self._show_error(f"Enter a whole-number quantity from 1 to {MAX_QUANTITY:,}.")
            return
        price = parse_amount(self._price_input.text())
        if price is None:
            self._show_error("Enter a unit price greater than 0 (e.g. 742,50 or 742.50).")
            return
        try:
            price = round_money(price, "Unit price")
        except ValueError as exc:
            self._show_error(str(exc))
            return
        try:
            order = purchase_order_repository.submit(
                barcode, self._supplier_input.text(), quantity, price, self._site,
                raised_by=current_session.actor(),
            )
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._show_error(str(exc))
            return
        self._last_order = order
        self._qty_input.clear()
        self._price_input.clear()
        self._show_result(order)
        self._refresh_orders_now()

    def _show_form(self) -> None:
        self._last_order = None
        self._awaiting_banner.hide()
        self._sent_banner.hide()
        self._form.show()
        self.reload_catalog()

    def _show_result(self, order: PurchaseOrder) -> None:
        self._form.hide()
        if order.status == "pending":
            self._sent_banner.hide()
            self._awaiting_number.setText(order.number)
            band = (
                f"{format_amount(order.range_min)} – {format_amount(order.range_max)}"
                if order.range_min is not None
                else "not set"
            )
            self._awaiting_values["Item"].setText(f"{order.product_barcode} · {order.quantity} units")
            self._awaiting_values["Unit price"].setText(format_amount(order.unit_price))
            self._awaiting_values["Safe band"].setText(band)
            self._awaiting_values["Order total"].setText(format_amount(order.total))
            self._awaiting_message.setText(
                f"On hold. Not sent to {order.supplier} until an administrator approves the price. "
                f"Submitted {local_time_text(order.created_at)}. This updates by itself once an "
                f"administrator decides."
            )
            self._awaiting_banner.show()
            return

        self._awaiting_banner.hide()
        if order.status == "rejected":
            note = f" Note: {html.escape(order.decision_note)}" if order.decision_note else ""
            self._set_notice(
                f"<b>{html.escape(order.number)} rejected</b> by an administrator · "
                f"not sent to {html.escape(order.supplier.rstrip('.'))}.{note}",
                positive=False,
            )
        else:
            approved = " (approved by an administrator)" if order.was_approved else ""
            self._set_notice(
                f"<b>{html.escape(order.number)} sent</b>{approved} · {order.quantity} × "
                f"{html.escape(order.product_barcode)} at {format_amount(order.unit_price)} to "
                f"{html.escape(order.supplier)}",
                positive=True,
            )
        self._sent_banner.show()

    # --- polling ---------------------------------------------------------

    def _fetch_orders(self) -> list[PurchaseOrder] | None:
        # Broad on purpose: this runs on a timer, and a transient
        # sqlite3 "database is locked" (not a DataAccessError) must just
        # mean "try again next tick", not an exception out of the poller.
        try:
            return purchase_order_repository.list_orders(site=self._site, limit=_TABLE_LIMIT)
        except Exception:
            return None

    def _refresh_orders_now(self) -> None:
        self._on_orders_fetched(self._fetch_orders())

    def _on_orders_fetched(self, orders: list[PurchaseOrder] | None) -> None:
        if orders is None:
            return
        self._fill_table(orders)
        if self._last_order is not None:
            latest = next((o for o in orders if o.id == self._last_order.id), None)
            if latest is not None and latest.status != self._last_order.status:
                self._last_order = latest
                self._show_result(latest)

    def _fill_table(self, orders: list[PurchaseOrder]) -> None:
        p = INDUSTRY_PALETTE
        self._table.setRowCount(len(orders))
        for row, order in enumerate(orders):
            values = [
                order.number,
                local_time_text(order.created_at),
                f"{order.product_barcode} · {order.product_name}",
                order.supplier,
                str(order.quantity),
                format_amount(order.unit_price),
                format_amount(order.total),
                status_text(order),
                order.decision_note or "",
            ]
            tooltip = decision_tooltip(order)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (4, 5, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column in (0, 6):
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == 7:
                    if order.status == "pending":
                        item.setBackground(QColor(p["text_primary"]))
                        item.setForeground(QColor(p["background"]))
                    elif order.status == "sent":
                        item.setForeground(QColor(p["accent"]))
                    else:
                        item.setForeground(QColor(p["text_secondary"]))
                if column in (7, 8) and tooltip:
                    item.setToolTip(tooltip)
                self._table.setItem(row, column, item)
