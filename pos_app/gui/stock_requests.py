"""Asking the depot for stock from the till (My Local Stock's two buttons).

* RequestStockDialog - pick a product (low / out ones first, each with how
  much is on the shelf), a quantity (pre-filled: back up to twice the
  reorder level, less what is already on its way) and an optional note;
  "Send request" writes it through stock_request_repository.create().
* MyRequestsDialog   - this dealership's requests, newest first, with
  their state (waiting / planned on SH-00012 / delivered / declined and
  why) and "Withdraw" on the ones still waiting.

Both talk to the database directly (like the till's other screens) and
keep their work in plain methods - submit(), withdraw() - so tests can
drive them without clicking.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from database import stock_request_repository
from database.exceptions import DATABASE_ERRORS
from pos_app.gui.components.organic import PillButton
from pos_app.gui.product_status import stock_status
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared import current_session
from shared.formatting import parse_db_timestamp
from shared.i18n import enum_label, tr
from shared.models import Product, StockRequest, suggested_request_qty

_ACCENT_600, _ACCENT_700 = "#b2622d", "#8c491a"
_NEUTRAL_800 = "#3a3631"


def _dialog_css() -> str:
    p = ORGANIC_PALETTE
    return (
        f"QDialog {{ background-color: {p['background']}; }} "
        f"QLabel {{ background: transparent; color: {p['text_primary']}; font-size: 16px; }} "
        f"QComboBox, QSpinBox, QLineEdit {{ background-color: {p['surface_raised']}; color: {p['text_primary']}; "
        f"border: 1px solid {p['border']}; border-radius: 14px; padding: 8px 12px; font-size: 17px; min-height: 30px; }}"
    )


def _title(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"font-family: {FONT_HEADING_CSS}; font-size: 30px; color: {ORGANIC_PALETTE['text_primary']};"
    )
    return label


def primary_button(text: str, height: int = 56) -> PillButton:
    return PillButton(text, ORGANIC_PALETTE["accent"], _ACCENT_600, _ACCENT_700, "#ffffff", height, 17)


def dark_button(text: str, height: int = 56) -> PillButton:
    p = ORGANIC_PALETTE
    return PillButton(text, p["text_primary"], _NEUTRAL_800, _ACCENT_700, p["background"], height, 17)


class RequestStockDialog(QDialog):
    """Ask the depot for one product. `products` is what this shelf shows
    (My Local Stock's list); inactive ones are left out."""

    def __init__(self, dealership_code: str, products: list[Product], incoming: dict[str, int] | None = None,
                 preselect: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = dealership_code
        self._incoming = {k.upper(): v for k, v in (incoming or {}).items()}
        order = {"out": 0, "low": 1, "ok": 2}
        # What this shelf carries, emptiest first; the rest of the catalogue after it.
        self._products = sorted((p for p in products if p.is_active),
                                key=lambda p: (not (p.stocked_here or p.stock_quantity > 0),
                                               order[stock_status(p)], p.name.lower()))
        self.request: StockRequest | None = None
        self.setWindowTitle(tr("pos.request.title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(_dialog_css())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(_title(tr("pos.request.title")))
        intro = QLabel(tr("pos.request.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {ORGANIC_PALETTE['text_secondary']}; font-size: 15px;")
        layout.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(12)
        self.product_combo = QComboBox()
        for product in self._products:
            self.product_combo.addItem(self._product_text(product), product.barcode)
        self.product_combo.currentIndexChanged.connect(self._suggest)
        form.addRow(tr("pos.request.product"), self.product_combo)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, stock_request_repository.MAX_REQUEST_QUANTITY)
        form.addRow(tr("pos.request.quantity"), self.quantity_spin)
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet(f"color: {ORGANIC_PALETTE['text_secondary']}; font-size: 14px;")
        form.addRow("", self.hint_label)
        self.note_input = QLineEdit()
        self.note_input.setMaxLength(stock_request_repository.MAX_NOTE_LENGTH)
        self.note_input.setPlaceholderText(tr("pos.request.note_ph"))
        form.addRow(tr("pos.request.note"), self.note_input)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {ORGANIC_PALETTE['alert_critical']}; font-size: 15px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        cancel = dark_button(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.send_button = primary_button(tr("pos.request.send"))
        self.send_button.clicked.connect(self.submit)
        self.send_button.setEnabled(bool(self._products))
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

        if preselect is not None:
            index = self.product_combo.findData(preselect)
            if index >= 0:
                self.product_combo.setCurrentIndex(index)
        self._suggest()

    def _product_text(self, product: Product) -> str:
        status = stock_status(product)
        state = "" if status == "ok" else f" · {tr('pos.stock.status_' + status)}"
        return f"{product.name} ({product.barcode}) · {tr('pos.request.on_shelf').format(n=product.stock_quantity)}{state}"

    def _current(self) -> Product | None:
        index = self.product_combo.currentIndex()
        return self._products[index] if 0 <= index < len(self._products) else None

    def _suggest(self) -> None:
        product = self._current()
        if product is None:
            self.hint_label.setText(tr("pos.request.nothing"))
            return
        coming = self._incoming.get(product.barcode.upper(), 0)
        suggested = suggested_request_qty(product.stock_quantity, product.critical_stock_level, coming)
        self.quantity_spin.setValue(max(1, suggested))
        parts = []
        if product.critical_stock_level:
            parts.append(tr("pos.stock.reorder_at").format(n=product.critical_stock_level))
        if coming:
            parts.append(tr("pos.request.coming").format(n=coming))
        self.hint_label.setText(" · ".join(parts))

    def submit(self) -> bool:
        product = self._current()
        if product is None:
            return False
        try:
            self.request = stock_request_repository.create(
                self._dealership_code, product.barcode, self.quantity_spin.value(), self.note_input.text(),
                current_session.actor(),
            )
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return False
        self.accept()
        return True


def request_state_text(request: StockRequest) -> str:
    """One line on where a request stands, in the till's language."""
    if request.status == "planned":
        if request.shipment_status == "delivered":
            return tr("pos.request.state_delivered").format(shipment=request.shipment_number)
        return tr("pos.request.state_planned").format(shipment=request.shipment_number)
    if request.status == "declined":
        reason = request.decision_note or tr("pos.request.no_reason")
        return tr("pos.request.state_declined").format(reason=reason)
    return enum_label("stock_request_status", request.status).capitalize()


class MyRequestsDialog(QDialog):
    """This dealership's requests, newest first; "Withdraw" on waiting ones."""

    def __init__(self, dealership_code: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = dealership_code
        self.requests: list[StockRequest] = []
        self.setWindowTitle(tr("pos.request.mine_title"))
        self.setModal(True)
        self.resize(720, 560)
        self.setStyleSheet(_dialog_css())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(_title(tr("pos.request.mine_title")))

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(host)
        layout.addWidget(scroll, stretch=1)

        close = dark_button(tr("common.close"))
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.reload()

    def reload(self) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            self.requests = stock_request_repository.list_requests(dealership_code=self._dealership_code, limit=50)
        except DATABASE_ERRORS:
            self.requests = []
        if not self.requests:
            empty = QLabel(tr("pos.request.none"))
            empty.setStyleSheet(f"color: {ORGANIC_PALETTE['text_secondary']};")
            self._rows.addWidget(empty)
        for request in self.requests:
            self._rows.addWidget(self._row(request))
        self._rows.addStretch(1)

    def _row(self, request: StockRequest) -> QWidget:
        p = ORGANIC_PALETTE
        row = QWidget()
        row.setObjectName("requestRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row.setStyleSheet(f"#requestRow {{ background-color: {p['surface_raised']}; border-radius: 18px; }}")
        line = QHBoxLayout(row)
        line.setContentsMargins(18, 12, 12, 12)
        text = QVBoxLayout()
        text.setSpacing(2)
        head = QLabel(f"{request.product_name} × {request.quantity}")
        head.setStyleSheet("font-weight: 700; font-size: 17px;")
        when = parse_db_timestamp(request.created_at).astimezone().strftime("%d.%m %H:%M") if request.created_at else ""
        sub = QLabel(f"{request.number} · {when} · {request_state_text(request)}")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {p['text_secondary']}; font-size: 14px;")
        text.addWidget(head)
        text.addWidget(sub)
        line.addLayout(text, stretch=1)
        if request.is_open:
            withdraw = dark_button(tr("pos.request.withdraw"), 44)
            withdraw.setFixedWidth(150)
            withdraw.clicked.connect(lambda _checked=False, rid=request.id: self.withdraw(rid))
            line.addWidget(withdraw)
        return row

    def withdraw(self, request_id: int) -> bool:
        try:
            stock_request_repository.cancel(request_id, current_session.actor())
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("pos.request.mine_title"), str(exc))
            self.reload()
            return False
        self.reload()
        return True
