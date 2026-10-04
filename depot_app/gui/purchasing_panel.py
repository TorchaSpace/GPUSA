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

Beyond the mockup (the order's life after approval): approved orders show
a "Receive delivery" action - a quantity dialog, booked into THIS depot's
warehouse in one transaction with the stock (purchase_order_repository.
receive_against_order) - and the status reads "Received · 40 of 100" /
"Partially received · 10 of 40". A held order can be cancelled by the
administrator or by the manager who raised it. Approved orders still
waiting for goods stay listed however old they are.

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
    QMessageBox,
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
from depot_app.gui.components.receive_delivery_dialog import ReceiveDeliveryDialog
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.formatting import format_int, local_datetime_text, local_time_text, parse_amount, round_money
from shared.currency import format_money
from shared.gui_kit.icon_kit import svg_to_icon
from shared.gui_kit.polling import PollingTimer
from shared.i18n import tr
from shared.models import PriceRange, Product, PurchaseOrder, StockLocation, hold_reason_for
from shared.textcase import upper
from shared import current_session

MAX_QUANTITY = purchase_order_repository.MAX_QUANTITY  # same cap the repository enforces
_MAX_QUANTITY_DIGITS = len(str(MAX_QUANTITY))
STATUS_POLL_INTERVAL_MS = 5000
_TABLE_LIMIT = 20

_ACTIONS_COLUMN = 9
_AWAITING_CAPTIONS = {
    "Item": "depot.purchasing.cap_item",
    "Unit price": "depot.purchasing.cap_unit_price",
    "Safe band": "depot.purchasing.cap_safe_band",
    "Order total": "depot.purchasing.cap_order_total",
}
_ROW_HEIGHT = 52  # room for the action buttons


def warehouse_code_of(site: str) -> str:
    """"WH-01" out of the "WH-01 · İstanbul Merkez" label orders are stamped with."""
    return (site or "").split("·")[0].strip().upper()


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
        who = tr("depot.purchasing.tip_by").format(name=order.decided_by) if order.decided_by else ""
        key = "depot.purchasing.tip_rejected" if order.status == "rejected" else "depot.purchasing.tip_approved"
        lines.append(tr(key).format(when=local_datetime_text(order.decided_at)) + who)
    if order.decision_note:
        lines.append(tr("depot.purchasing.tip_note").format(note=order.decision_note))
    return "\n".join(lines)


def status_text(order: PurchaseOrder) -> str:
    if order.status in ("received", "partially_received"):
        return tr(f"depot.po.status.{order.status}").format(received=order.received_qty, quantity=order.quantity)
    if order.status == "sent" and order.was_approved:
        return tr("depot.po.status.approved")
    return tr(f"depot.po.status.{order.status}")


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
            painter.drawText(track, Qt.AlignCenter, tr("depot.purchasing.no_safe_range"))
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
            text = format_money(self._price)
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
    label = QLabel(upper(text))
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

    def __init__(self, site: str, parent: QWidget | None = None, warehouse_code: str | None = None):
        super().__init__(parent)
        self._site = site
        # The warehouse deliveries are booked into: this depot's own.
        self._warehouse_code = (warehouse_code or warehouse_code_of(site)).upper()
        self._orders: list[PurchaseOrder] = []
        self._action_buttons: dict[tuple[str, int], IndustryButton] = {}
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
        title = QLabel(upper(tr("depot.po.status.pending")))
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
            # `key` stays an English id (see _show_result); only the caption is translated.
            caption = QLabel(upper(tr(_AWAITING_CAPTIONS[key])))
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

        new_order = IndustryButton(tr("depot.purchasing.new_order"), variant="ghost")
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
        new_order = IndustryButton(tr("depot.purchasing.new_order"), variant="ghost")
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
        left.addWidget(_kicker(tr("depot.purchasing.new_purchase").format(site=self._site)))

        left.addWidget(_kicker(tr("depot.purchasing.cap_item")))
        self._item_input = QComboBox()
        self._item_input.setStyleSheet(f"QComboBox {{ {_input_style(16)} min-height: 34px; }}")
        self._item_input.currentIndexChanged.connect(self._on_item_changed)
        left.addWidget(self._item_input)

        left.addWidget(_kicker(tr("depot.purchasing.cap_supplier")))
        self._supplier_input = QLineEdit()
        self._supplier_input.setStyleSheet(_input_style(16))
        left.addWidget(self._supplier_input)

        numbers = QGridLayout()
        numbers.setHorizontalSpacing(12)
        numbers.addWidget(_kicker(tr("depot.purchasing.cap_quantity")), 0, 0)
        numbers.addWidget(_kicker(tr("depot.purchasing.cap_unit_price")), 0, 1)
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
        total_row.addWidget(_kicker(tr("depot.purchasing.cap_order_total")))
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
        band_header.addWidget(_kicker(tr("depot.purchasing.band_title")), stretch=1)
        read_only = QLabel(upper(tr("depot.purchasing.read_only")))
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
        per_unit = QLabel(tr("depot.purchasing.per_unit"))
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
        approved = QLabel(tr("depot.purchasing.approved_band"))
        self._scale_label = QLabel("")
        for label in (zero, approved, self._scale_label):
            label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']}; border: none;")
        scale_row.addWidget(zero)
        scale_row.addStretch(1)
        scale_row.addWidget(approved)
        scale_row.addStretch(1)
        scale_row.addWidget(self._scale_label)
        right.addLayout(scale_row)

        only_admin = QLabel(tr("depot.purchasing.only_admin"))
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

        self._send_button = IndustryButton(tr("depot.purchasing.send"), variant="accent")
        self._send_button.setMinimumHeight(54)
        self._send_button.clicked.connect(self._submit)
        self._hold_button = IndustryButton(tr("depot.purchasing.hold"), variant="primary")
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
        layout.addWidget(_kicker(tr("depot.purchasing.orders_title").format(site=self._site)))
        self._action_label = QLabel()
        self._action_label.setTextFormat(Qt.PlainText)
        self._action_label.setWordWrap(True)
        self._action_label.hide()
        layout.addWidget(self._action_label)
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels(
            [tr(f"depot.purchasing.col.{c}") for c in
             ("po", "time", "item", "supplier", "qty", "unit", "total", "status", "note")]
            + [tr("depot.po.col_actions")]
        )
        self._table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_ACTIONS_COLUMN, QHeaderView.ResizeToContents)
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
            self._show_error(tr("depot.purchasing.refresh_failed").format(error=exc))

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

        self._total_label.setText(format_money(_order_total(price or 0, quantity or 0)))
        self._band_min.setText(format_money(price_range.min_unit_price) if price_range else "—")
        self._band_max.setText(format_money(price_range.max_unit_price) if price_range else "—")
        self._band_bar.set_state(price_range, price)
        self._scale_label.setText(format_money(self._band_bar.scale_max()) if price_range else "")

        has_product = self._selected_barcode() is not None
        reason = hold_reason_for(price_range, price) if (price and price > 0) else None
        if not has_product:
            self._set_verdict(
                tr("depot.purchasing.v_no_products"), tr("depot.purchasing.v_no_products_body"), warning=True
            )
        elif price is None or price <= 0:
            if price_range is None:
                self._set_verdict(
                    tr("depot.purchasing.no_safe_range"),
                    tr("depot.purchasing.v_no_range_any"),
                    warning=True,
                )
            else:
                self._set_verdict("", tr("depot.purchasing.v_enter_price"), warning=None)
        elif reason == "above_range":
            over = price - price_range.max_unit_price
            pct = round(over / price_range.max_unit_price * 100) if price_range.max_unit_price else 0
            self._set_verdict(
                tr("depot.purchasing.v_above_title"),
                tr("depot.purchasing.v_above").format(
                    price=format_money(price), over=format_money(over), pct=pct,
                    ceiling=format_money(price_range.max_unit_price)),
                warning=True,
            )
        elif reason == "below_range":
            self._set_verdict(
                tr("depot.purchasing.v_below_title"),
                tr("depot.purchasing.v_below").format(
                    price=format_money(price), floor=format_money(price_range.min_unit_price)),
                warning=True,
            )
        elif reason == "no_range":
            self._set_verdict(
                tr("depot.purchasing.no_safe_range"),
                tr("depot.purchasing.v_no_range_this"),
                warning=True,
            )
        else:
            self._set_verdict("", tr("depot.purchasing.v_within"), warning=False)

        held = reason is not None or (has_product and price_range is None)
        self._send_button.setVisible(not held)
        self._hold_button.setVisible(held)
        enabled = has_product
        self._send_button.setEnabled(enabled)
        self._hold_button.setEnabled(enabled)

    def _set_verdict(self, title: str, body: str, warning: bool | None) -> None:
        p = INDUSTRY_PALETTE
        self._verdict_title.setText(upper(title))
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
            self._show_error(tr("depot.purchasing.err_pick_item"))
            return
        quantity = self._typed_quantity()
        if not quantity:
            self._show_error(tr("depot.purchasing.err_quantity").format(max=f"{MAX_QUANTITY:,}"))
            return
        price = parse_amount(self._price_input.text())
        if price is None:
            self._show_error(tr("depot.purchasing.err_price"))
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
                f"{format_money(order.range_min)} – {format_money(order.range_max)}"
                if order.range_min is not None
                else tr("depot.purchasing.band_not_set")
            )
            self._awaiting_values["Item"].setText(
                tr("depot.purchasing.awaiting_item").format(sku=order.product_barcode, quantity=order.quantity)
            )
            self._awaiting_values["Unit price"].setText(format_money(order.unit_price))
            self._awaiting_values["Safe band"].setText(band)
            self._awaiting_values["Order total"].setText(format_money(order.total))
            self._awaiting_message.setText(
                tr("depot.purchasing.awaiting_msg").format(
                    supplier=order.supplier, time=local_time_text(order.created_at))
            )
            self._awaiting_banner.show()
            return

        self._awaiting_banner.hide()
        if order.status == "rejected":
            note = (
                tr("depot.purchasing.rejected_note").format(note=html.escape(order.decision_note))
                if order.decision_note else ""
            )
            self._set_notice(
                tr("depot.purchasing.banner_rejected").format(
                    number=html.escape(order.number),
                    supplier=html.escape(order.supplier.rstrip(".")),
                    note=note),
                positive=False,
            )
        elif order.status == "cancelled":
            self._set_notice(
                tr("depot.po.banner_cancelled").format(number=html.escape(order.number),
                                                       supplier=html.escape(order.supplier.rstrip("."))),
                positive=False,
            )
        elif order.status in ("received", "partially_received"):
            self._set_notice(
                tr("depot.po.banner_received").format(
                    number=html.escape(order.number), received=order.received_qty, quantity=order.quantity,
                    supplier=html.escape(order.supplier)),
                positive=True,
            )
        else:
            approved = tr("depot.purchasing.approved_suffix") if order.was_approved else ""
            self._set_notice(
                tr("depot.purchasing.banner_sent").format(
                    number=html.escape(order.number), approved=approved, quantity=order.quantity,
                    sku=html.escape(order.product_barcode), price=format_money(order.unit_price),
                    supplier=html.escape(order.supplier)),
                positive=True,
            )
        self._sent_banner.show()

    # --- polling ---------------------------------------------------------

    def _fetch_orders(self) -> list[PurchaseOrder] | None:
        # Broad on purpose: this runs on a timer, and a transient
        # sqlite3 "database is locked" (not a DataAccessError) must just
        # mean "try again next tick", not an exception out of the poller.
        try:
            recent = purchase_order_repository.list_orders(site=self._site, limit=_TABLE_LIMIT)
            # An approved order still waiting for its goods must stay on
            # screen however many newer orders there are.
            waiting = [
                o for status in ("sent", "partially_received")
                for o in purchase_order_repository.list_orders(status=status, site=self._site)
            ]
        except Exception:
            return None
        shown = {o.id for o in recent}
        merged = recent + [o for o in waiting if o.id not in shown]
        return sorted(merged, key=lambda o: o.id, reverse=True)

    def _refresh_orders_now(self) -> None:
        self._on_orders_fetched(self._fetch_orders())

    def _on_orders_fetched(self, orders: list[PurchaseOrder] | None) -> None:
        if orders is None:
            return
        self._orders = orders
        self._fill_table(orders)
        if self._last_order is not None:
            latest = next((o for o in orders if o.id == self._last_order.id), None)
            if latest is not None and latest.status != self._last_order.status:
                self._last_order = latest
                self._show_result(latest)

    def _fill_table(self, orders: list[PurchaseOrder]) -> None:
        p = INDUSTRY_PALETTE
        self._table.setRowCount(0)  # drops the old rows' buttons with them
        self._action_buttons = {}
        self._table.setRowCount(len(orders))
        for row, order in enumerate(orders):
            values = [
                order.number,
                local_time_text(order.created_at),
                f"{order.product_barcode} · {order.product_name}",
                order.supplier,
                str(order.quantity),
                format_money(order.unit_price),
                format_money(order.total),
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
                    elif order.status in ("sent", "partially_received", "received"):
                        item.setForeground(QColor(p["accent"]))
                    else:
                        item.setForeground(QColor(p["text_secondary"]))
                if column in (7, 8) and tooltip:
                    item.setToolTip(tooltip)
                self._table.setItem(row, column, item)
            self._table.setCellWidget(row, _ACTIONS_COLUMN, self._build_actions(order))

    # --- receiving and cancelling ----------------------------------------

    def _build_actions(self, order: PurchaseOrder) -> QWidget | None:
        """The row's buttons: "Receive delivery" while goods are still due on
        an approved order, "Cancel" on a held order this person may cancel."""
        buttons: list[tuple[str, IndustryButton]] = []
        if order.can_receive:
            button = IndustryButton(tr("depot.po.receive_action"), variant="accent")
            button.clicked.connect(lambda _checked=False, order_id=order.id: self._receive(order_id))
            buttons.append(("receive", button))
        if self._can_cancel(order):
            button = IndustryButton(tr("depot.po.cancel_action"), variant="ghost")
            button.clicked.connect(lambda _checked=False, order_id=order.id: self._cancel(order_id))
            buttons.append(("cancel", button))
        if not buttons:
            return None
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(6)
        for kind, button in buttons:
            button.setMinimumHeight(36)
            row.addWidget(button)
            self._action_buttons[(kind, order.id)] = button
        return holder

    def action_button(self, kind: str, order_id: int) -> IndustryButton | None:
        """The "receive" / "cancel" button on an order's row, or None."""
        return self._action_buttons.get((kind, order_id))

    @staticmethod
    def _can_cancel(order: PurchaseOrder) -> bool:
        """A held order: any administrator, or the depot manager who raised
        it (the repository enforces the same rule from the database)."""
        session = current_session.get()
        if session is None or order.status != "pending":
            return False
        if session.role == "admin":
            return True
        return session.role == "depot_manager" and order.raised_by_badge == session.badge_id.strip().upper()

    def _set_action_message(self, text: str, ok: bool) -> None:
        p = INDUSTRY_PALETTE
        if ok:
            self._action_label.setStyleSheet(
                f"font-size: 13px; color: {p['accent_900']}; background-color: {p['accent_100']}; "
                f"border: 1.5px solid {p['accent']}; padding: 8px 12px;")
        else:
            self._action_label.setStyleSheet(
                f"font-size: 13px; color: {p['text_primary']}; background-color: #fff6d6; "
                f"border: 1px solid #f4b400; padding: 8px 12px;")
        self._action_label.setText(text)
        self._action_label.show()

    def _order_by_id(self, order_id: int) -> PurchaseOrder | None:
        return next((o for o in self._orders if o.id == order_id), None)

    def _ask_receive_quantity(self, order: PurchaseOrder) -> int | None:
        """The units that arrived, or None if the dialog was cancelled.
        Separate method so tests can answer it without a modal dialog."""
        dialog = ReceiveDeliveryDialog(order, self._warehouse_code, self)
        return dialog.quantity() if dialog.exec() else None

    def _confirm_cancel(self, order: PurchaseOrder) -> bool:
        """Separate method so tests can answer it without a modal dialog."""
        answer = QMessageBox.question(
            self, tr("depot.po.cancel_title"),
            tr("depot.po.cancel_q").format(number=order.number, supplier=order.supplier,
                                           quantity=format_int(order.quantity)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _receive(self, order_id: int) -> None:
        order = self._order_by_id(order_id)
        if order is None:
            return
        quantity = self._ask_receive_quantity(order)
        if not quantity:
            return
        try:
            done = purchase_order_repository.receive_against_order(
                order_id, quantity, current_session.actor(), StockLocation.warehouse(self._warehouse_code)
            )
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._set_action_message(tr("depot.po.receive_failed").format(number=order.number, error=exc), ok=False)
            self._refresh_orders_now()
            return
        key = "depot.po.received_all" if done.status == "received" else "depot.po.received_part"
        self._set_action_message(
            tr(key).format(number=done.number, quantity=format_int(quantity), warehouse=self._warehouse_code,
                           received=format_int(done.received_qty), ordered=format_int(done.quantity)),
            ok=True,
        )
        self._refresh_orders_now()

    def _cancel(self, order_id: int) -> None:
        order = self._order_by_id(order_id)
        if order is None or not self._confirm_cancel(order):
            return
        try:
            done = purchase_order_repository.cancel_order(order_id, current_session.actor())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._set_action_message(tr("depot.po.cancel_failed").format(number=order.number, error=exc), ok=False)
            self._refresh_orders_now()
            return
        self._set_action_message(tr("depot.po.cancelled").format(number=done.number), ok=True)
        if self._last_order is not None and self._last_order.id == done.id:
            self._show_form()  # the "awaiting approval" banner is about an order that no longer is
        self._refresh_orders_now()
