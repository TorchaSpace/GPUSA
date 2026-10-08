"""Sales: the till's recent sales, refunds and the end-of-day count.

* SalesPage        - this dealership's sales from the last 7 days (newest first)
  with what was refunded; "Return items" opens a ReturnDialog on the selected
  sale, "Close the day" opens the DayCloseDialog.
* ReturnDialog     - pick the units coming back (at most what is still out on
  that sale), whether they go back on the shelf or are written off (damaged),
  why - and a manager or administrator signs it off with their badge and PIN
  (the cashier cannot approve their own refund). The work is in submit().
* DayCloseDialog   - what the day's sales say should be in the drawer (cash
  sales less cash refunds), the cashier types what they counted, and the
  difference is stored (database/day_close_repository.py).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import account_repository, day_close_repository, sale_return_repository, transaction_repository
from database.exceptions import DATABASE_ERRORS
from pos_app.gui.stock_requests import _dialog_css, _title, dark_button, primary_button
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared import current_session
from shared.auth import AREA_DEPOT_CONSOLE
from shared.currency import format_money
from shared.i18n import tr
from shared.models import DayClose, SaleReturn, StockLocation, Transaction, UNASSIGNED

RECENT_DAYS = 7


def _error_label() -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {ORGANIC_PALETTE['alert_critical']}; font-size: 15px;")
    label.hide()
    return label


def _method_text(method: str | None) -> str:
    return tr(f"pos.sale.{method}") if method in ("card", "cash") else tr("pos.sales.method_none")


class ReturnDialog(QDialog):
    """Refund part or all of one sale. `terminal` names this till in the sign-in log."""

    def __init__(self, transaction: Transaction, requested_by=None, terminal: str = "POS", parent: QWidget | None = None):
        super().__init__(parent)
        self.transaction = transaction
        self.sale_return: SaleReturn | None = None
        self._requested_by = requested_by
        self._terminal = terminal
        self._lines = sale_return_repository.returnable_lines(transaction.id)
        self.setWindowTitle(tr("pos.sales.return_title"))
        self.setModal(True)
        self.setMinimumWidth(640)
        self.setStyleSheet(_dialog_css())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(_title(tr("pos.sales.return_title")))
        intro = QLabel(tr("pos.sales.return_intro").format(number=f"#{transaction.id}"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {ORGANIC_PALETTE['text_secondary']}; font-size: 15px;")
        layout.addWidget(intro)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.quantity_spins: dict[str, QSpinBox] = {}
        self.restock_boxes: dict[str, QCheckBox] = {}
        for row, line in enumerate(self._lines):
            name = QLabel(f"{line.product_name_at_sale} · {format_money(line.unit_price_at_sale)}")
            left = QLabel(tr("pos.sales.returnable").format(n=line.available))
            left.setStyleSheet(f"color: {ORGANIC_PALETTE['text_secondary']}; font-size: 14px;")
            spin = QSpinBox()
            spin.setRange(0, max(line.available, 0))
            spin.setEnabled(line.available > 0)
            box = QCheckBox(tr("pos.sales.restock"))
            box.setChecked(True)
            grid.addWidget(name, row, 0)
            grid.addWidget(left, row, 1)
            grid.addWidget(spin, row, 2)
            grid.addWidget(box, row, 3)
            self.quantity_spins[line.product_barcode] = spin
            self.restock_boxes[line.product_barcode] = box
        layout.addLayout(grid)

        form = QFormLayout()
        form.setSpacing(10)
        self.reason_input = QLineEdit()
        self.reason_input.setMaxLength(sale_return_repository.MAX_REASON_LENGTH)
        self.reason_input.setPlaceholderText(tr("pos.sales.reason_ph"))
        form.addRow(tr("pos.sales.reason"), self.reason_input)
        self.badge_input = QLineEdit()
        self.badge_input.setPlaceholderText(tr("pos.sales.approver_badge"))
        form.addRow(tr("pos.sales.approver"), self.badge_input)
        self.pin_input = QLineEdit()
        self.pin_input.setEchoMode(QLineEdit.Password)
        self.pin_input.setPlaceholderText(tr("pos.sales.approver_pin"))
        form.addRow("", self.pin_input)
        layout.addLayout(form)

        self.error_label = _error_label()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = dark_button(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.send_button = primary_button(tr("pos.sales.refund"))
        self.send_button.clicked.connect(self.submit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.send_button)
        layout.addLayout(buttons)

    def refund_total(self) -> float:
        prices = {line.product_barcode: line.unit_price_at_sale for line in self._lines}
        return round(sum(prices[b] * spin.value() for b, spin in self.quantity_spins.items()), 2)

    def submit(self) -> bool:
        lines = [(barcode, spin.value(), self.restock_boxes[barcode].isChecked())
                 for barcode, spin in self.quantity_spins.items() if spin.value() > 0]
        try:
            approver = account_repository.authenticate(
                self.badge_input.text(), self.pin_input.text(), AREA_DEPOT_CONSOLE, self._terminal,
                event="pin_confirmed",
            ).actor
            self.sale_return = sale_return_repository.create(
                self.transaction.id, lines, self.reason_input.text(), approver, self._requested_by)
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.pin_input.clear()
            self.error_label.setText(str(exc))
            self.error_label.show()
            return False
        self.accept()
        return True


class DayCloseDialog(QDialog):
    """Count the drawer against what the day's sales say it should hold."""

    def __init__(self, dealership_code: str | None, day: date | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = dealership_code
        self._day = day or date.today()
        self.closed: DayClose | None = None
        self.summary = day_close_repository.summarize(dealership_code, self._day)
        s = self.summary
        self.setWindowTitle(tr("pos.sales.close_title"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(_dialog_css())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(_title(tr("pos.sales.close_title")))
        lines = [
            (tr("pos.sales.sum_sales").format(n=s.sales_count), format_money(s.cash_sales + s.card_sales + s.other_sales)),
            (tr("pos.sales.sum_cash"), format_money(s.cash_sales)),
            (tr("pos.sales.sum_card"), format_money(s.card_sales)),
            (tr("pos.sales.sum_other"), format_money(s.other_sales)),
            (tr("pos.sales.sum_refunds").format(n=s.refunds_count), format_money(-(s.cash_refunds + s.card_refunds))),
            (tr("pos.sales.sum_expected"), format_money(s.expected_cash)),
        ]
        grid = QGridLayout()
        for row, (label, value) in enumerate(lines):
            grid.addWidget(QLabel(label), row, 0)
            amount = QLabel(value)
            amount.setAlignment(Qt.AlignRight)
            if row == len(lines) - 1:
                amount.setStyleSheet("font-weight: 700;")
            grid.addWidget(amount, row, 1)
        layout.addLayout(grid)

        form = QFormLayout()
        self.counted_spin = QDoubleSpinBox()
        self.counted_spin.setRange(0, 100_000_000)
        self.counted_spin.setDecimals(2)
        self.counted_spin.setValue(s.expected_cash)
        self.counted_spin.valueChanged.connect(self._show_difference)
        form.addRow(tr("pos.sales.counted"), self.counted_spin)
        self.note_input = QLineEdit()
        self.note_input.setMaxLength(day_close_repository.MAX_NOTE_LENGTH)
        self.note_input.setPlaceholderText(tr("pos.sales.close_note_ph"))
        form.addRow(tr("pos.sales.close_note"), self.note_input)
        layout.addLayout(form)

        self.difference_label = QLabel("")
        self.difference_label.setStyleSheet("font-size: 17px; font-weight: 700;")
        layout.addWidget(self.difference_label)
        self.error_label = _error_label()
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = dark_button(tr("common.cancel"))
        cancel.clicked.connect(self.reject)
        self.close_button = primary_button(tr("pos.sales.close_button"))
        self.close_button.clicked.connect(self.submit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._show_difference()

    def difference(self) -> float:
        return round(self.counted_spin.value() - self.summary.expected_cash, 2)

    def _show_difference(self) -> None:
        diff = self.difference()
        p = ORGANIC_PALETTE
        if diff == 0:
            self.difference_label.setText(tr("pos.sales.diff_ok"))
            color = p["text_primary"]
        else:
            self.difference_label.setText(tr("pos.sales.diff_short" if diff < 0 else "pos.sales.diff_over")
                                          .format(amount=format_money(abs(diff))))
            color = p["alert_critical"]
        self.difference_label.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {color};")

    def submit(self) -> bool:
        try:
            self.closed = day_close_repository.close(
                self._dealership_code, self.counted_spin.value(), self._day, current_session.actor(),
                self.note_input.text())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return False
        self.accept()
        return True


class SalesPage(QWidget):
    stock_changed = Signal()  # a refund put units back on the shelf

    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = location.code if location.kind == "dealership" else None
        self.transactions: list[Transaction] = []
        self._refunded: dict[int, float] = {}
        p = ORGANIC_PALETTE
        self.setObjectName("salesPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#salesPage {{ background-color: {p['background']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 20)
        outer.setSpacing(14)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        subtitle = QLabel(tr("pos.sales.subtitle").format(days=RECENT_DAYS))
        subtitle.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        title = QLabel(tr("pos.sales.title"))
        title.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 40px; color: {p['text_primary']};")
        titles.addWidget(subtitle)
        titles.addWidget(title)
        head.addLayout(titles)
        head.addStretch(1)
        self.return_button = primary_button(tr("pos.sales.return_button"), 52)
        self.return_button.clicked.connect(self.open_return)
        self.close_day_button = dark_button(tr("pos.sales.close_button"), 52)
        self.close_day_button.clicked.connect(self.open_close)
        head.addWidget(self.return_button)
        head.addWidget(self.close_day_button)
        outer.addLayout(head)

        self.message = QLabel("")
        self.message.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        outer.addWidget(self.message)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([tr(f"pos.sales.col_{k}") for k in (
            "no", "time", "cashier", "items", "method", "total", "refunded")])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setStyleSheet(
            f"QTableWidget {{ background-color: {p['surface_raised']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; font-size: 16px; gridline-color: {p['border']}; }} "
            f"QHeaderView::section {{ background-color: {p['background']}; color: {p['text_secondary']}; "
            f"border: none; padding: 8px; font-size: 14px; }}")
        self.table.itemSelectionChanged.connect(self._update_buttons)
        outer.addWidget(self.table, stretch=1)
        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not event.spontaneous():
            self.reload()

    def reload(self) -> None:
        end = datetime.now() + timedelta(days=1)
        start = datetime.combine(date.today() - timedelta(days=RECENT_DAYS - 1), datetime.min.time())
        try:
            rows = transaction_repository.list_between(start, end, net_of_returns=False)
        except DATABASE_ERRORS:
            return  # a locked database on a refresh: keep what is shown
        self.transactions = sorted((t for t in rows if t.dealership_code == self._dealership_code),
                                   key=lambda t: t.id, reverse=True)
        self._refunded = {}
        for refund in sale_return_repository_all(self.transactions):
            self._refunded[refund.transaction_id] = self._refunded.get(refund.transaction_id, 0.0) + refund.total
        self.table.setRowCount(len(self.transactions))
        for row, t in enumerate(self.transactions):
            refunded = self._refunded.get(t.id, 0.0)
            values = [f"#{t.id}", t.created_at.strftime("%d.%m %H:%M") if t.created_at else "", t.cashier or "—",
                      str(sum(i.quantity for i in t.items)), _method_text(t.payment_method),
                      format_money(t.total), format_money(refunded) if refunded else ""]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
        self._update_buttons()

    def selected_transaction(self) -> Transaction | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self.transactions):
            return None
        return self.transactions[rows[0].row()]

    def _update_buttons(self) -> None:
        self.return_button.setEnabled(self.selected_transaction() is not None)

    def return_dialog(self) -> ReturnDialog | None:
        sale = self.selected_transaction()
        if sale is None:
            return None
        return ReturnDialog(sale, current_session.actor(), "POS refund", self)

    def open_return(self) -> None:
        dialog = self.return_dialog()
        if dialog is not None and dialog.exec() and dialog.sale_return is not None:
            self.message.setText(tr("pos.sales.refunded").format(
                number=dialog.sale_return.number, amount=format_money(dialog.sale_return.total)))
            self.reload()
            self.stock_changed.emit()

    def open_close(self) -> None:
        dialog = DayCloseDialog(self._dealership_code, parent=self)
        if dialog.exec() and dialog.closed is not None:
            self.message.setText(tr("pos.sales.closed").format(
                diff=format_money(dialog.closed.difference)))


def sale_return_repository_all(transactions: list[Transaction]) -> list[SaleReturn]:
    """Every refund of the given sales (a refund may be on a later day than its sale)."""
    result: list[SaleReturn] = []
    for t in transactions:
        result.extend(sale_return_repository.list_for_transaction(t.id))
    return result
