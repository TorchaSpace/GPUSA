"""Console > Shipments' "Dealership needs" panel: what the shops are asking
for and which shelves are running low, so a shipment can be planned from it.

* Requests - every open stock request (oldest first), raised from a POS
  till's My Local Stock. "Plan shipment" takes ALL open requests of the
  selected request's dealership into the New shipment form (destination +
  one line each); creating the shipment marks them planned. "Decline"
  turns the selected one down with the reason typed beside it.
* Low at dealerships - products at/below their reorder level on an active
  dealership's shelf, with what is already coming (active shipments) and
  asked for (open requests). "Add to shipment" puts the selected shop and
  the suggested quantity (back up to twice the reorder level, less what is
  coming / asked for) into the form.

Real: database.stock_request_repository. The panel only emits
`plan_requested(dealership_code, lines, request_ids)`; ShipmentsPage fills
its form from it, so creating the shipment stays in one place.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import stock_request_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import INDUSTRY_PALETTE
from shared import current_session
from shared.formatting import local_datetime_text
from shared.i18n import tr
from shared.models import DealershipShortage, StockRequest
from shared.textcase import upper


def _kicker(text: str) -> QLabel:
    label = QLabel(upper(text))
    label.setStyleSheet(f"font-size: 12px; letter-spacing: 1px; color: {INDUSTRY_PALETTE['text_secondary']};")
    return label


def _table(headers: list[str]) -> QTableWidget:
    p = INDUSTRY_PALETTE
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setStyleSheet(
        f"""
        QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
            border: 1px solid {p['border']}; gridline-color: {p['border']}; font-size: 13px; }}
        QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
            border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
        QTableWidget::item:selected {{ background-color: {p['accent_100']}; color: {p['text_primary']}; }}
        """
    )
    header = table.horizontalHeader()
    for column in range(len(headers)):
        header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
    table.setMaximumHeight(190)
    return table


class DealershipNeedsPanel(BlueprintFrame):
    # (dealership code, [(barcode, label, qty)], request ids to tie to the shipment)
    plan_requested = Signal(str, list, list)

    def __init__(self, parent: QWidget | None = None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        self.setObjectName("dealershipNeeds")
        self.setStyleSheet(f"#dealershipNeeds {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        self.requests: list[StockRequest] = []
        self.shortages: list[DealershipShortage] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(8)

        self._requests_kicker = _kicker(tr("depot.needs.requests").format(n=0))
        layout.addWidget(self._requests_kicker)
        self.requests_table = _table([tr(f"depot.needs.col_{k}") for k in
                                      ("no", "dealer", "product", "qty", "asked", "by", "note")])
        self.requests_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.requests_table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.requests_table)
        actions = QHBoxLayout()
        self.plan_button = IndustryButton(tr("depot.needs.plan"), variant="accent")
        self.plan_button.clicked.connect(self.plan_selected_request)
        self.reason_input = QLineEdit()
        self.reason_input.setPlaceholderText(tr("depot.needs.reason_ph"))
        self.reason_input.setMaxLength(stock_request_repository.MAX_NOTE_LENGTH)
        self.reason_input.setStyleSheet(
            f"background-color: {p['surface_raised']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; border-radius: 0; padding: 6px 8px; font-size: 14px;"
        )
        self.decline_button = IndustryButton(tr("depot.needs.decline"), variant="ghost")
        self.decline_button.clicked.connect(self.decline_selected)
        actions.addWidget(self.plan_button)
        actions.addStretch(1)
        actions.addWidget(self.reason_input, stretch=2)
        actions.addWidget(self.decline_button)
        layout.addLayout(actions)

        self._low_kicker = _kicker(tr("depot.needs.low").format(n=0))
        layout.addWidget(self._low_kicker)
        self.low_table = _table([tr(f"depot.needs.col_{k}") for k in
                                 ("dealer", "product", "on_hand", "reorder", "coming", "requested")])
        self.low_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.low_table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.low_table)
        low_actions = QHBoxLayout()
        self.add_low_button = IndustryButton(tr("depot.needs.add_low"), variant="ghost")
        self.add_low_button.clicked.connect(self.plan_selected_shortage)
        low_actions.addWidget(self.add_low_button)
        low_actions.addStretch(1)
        layout.addLayout(low_actions)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        layout.addWidget(self.message)
        self.reload()

    # --- data ----------------------------------------------------------------
    def reload(self) -> None:
        try:
            requests = stock_request_repository.list_open()
            shortages = stock_request_repository.dealership_shortages()
        except DATABASE_ERRORS:  # a transient lock on a timer tick: keep what is shown
            return
        selected_request = self.selected_request()
        self.requests, self.shortages = requests, shortages
        self._render_requests()
        self._render_low()
        if selected_request is not None:
            for row, request in enumerate(self.requests):
                if request.id == selected_request.id:
                    self.requests_table.selectRow(row)
        self._update_buttons()

    def _render_requests(self) -> None:
        self._requests_kicker.setText(upper(tr("depot.needs.requests").format(n=len(self.requests))))
        table = self.requests_table
        table.blockSignals(True)
        table.setRowCount(len(self.requests))
        for row, request in enumerate(self.requests):
            values = [request.number, f"{request.dealership_code} · {request.dealership_name}", request.product_name,
                      str(request.quantity), local_datetime_text(request.created_at), request.requested_by or "",
                      request.note or ""]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row, column, item)
        table.blockSignals(False)

    def _render_low(self) -> None:
        p = INDUSTRY_PALETTE
        open_count = sum(1 for s in self.shortages if not s.covered)
        self._low_kicker.setText(upper(tr("depot.needs.low").format(n=open_count)))
        table = self.low_table
        table.blockSignals(True)
        table.setRowCount(len(self.shortages))
        for row, shortage in enumerate(self.shortages):
            values = [f"{shortage.dealership_code} · {shortage.dealership_name}", shortage.product_name,
                      str(shortage.on_hand), str(shortage.reorder_level), str(shortage.incoming_qty or ""),
                      str(shortage.requested_qty or "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 2 and not shortage.covered:
                    item.setBackground(QColor(p["text_primary"]))
                    item.setForeground(QColor(p["background"]))
                if shortage.covered:
                    item.setForeground(QColor(p["text_secondary"]))
                table.setItem(row, column, item)
        table.blockSignals(False)

    def selected_request(self) -> StockRequest | None:
        rows = self.requests_table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self.requests):
            return None
        return self.requests[rows[0].row()]

    def selected_shortage(self) -> DealershipShortage | None:
        rows = self.low_table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self.shortages):
            return None
        return self.shortages[rows[0].row()]

    def _update_buttons(self) -> None:
        has_request = self.selected_request() is not None
        self.plan_button.setEnabled(has_request)
        self.decline_button.setEnabled(has_request)
        self.add_low_button.setEnabled(self.selected_shortage() is not None)

    # --- actions -------------------------------------------------------------
    def plan_selected_request(self) -> None:
        """Every open request of the selected request's dealership into the form."""
        chosen = self.selected_request()
        if chosen is None:
            return
        mine = [r for r in self.requests if r.dealership_code == chosen.dealership_code]
        lines = [(r.product_barcode, f"{r.product_barcode} · {r.product_name}", r.quantity) for r in mine]
        self.message.setText(tr("depot.needs.planned_hint").format(n=len(mine), dealer=chosen.dealership_name))
        self.plan_requested.emit(chosen.dealership_code, lines, [r.id for r in mine])

    def plan_selected_shortage(self) -> None:
        shortage = self.selected_shortage()
        if shortage is None:
            return
        quantity = max(1, shortage.suggested_qty)
        label = f"{shortage.product_barcode} · {shortage.product_name}"
        self.message.setText(tr("depot.needs.low_hint").format(qty=quantity, product=shortage.product_name,
                                                               dealer=shortage.dealership_name))
        self.plan_requested.emit(shortage.dealership_code, [(shortage.product_barcode, label, quantity)], [])

    def decline_selected(self) -> bool:
        request = self.selected_request()
        if request is None:
            return False
        try:
            declined = stock_request_repository.decline(request.id, self.reason_input.text(), current_session.actor())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self.message.setText(str(exc))
            self.reload()
            return False
        self.reason_input.clear()
        self.message.setText(tr("depot.needs.declined").format(number=declined.number, dealer=declined.dealership_name))
        self.reload()
        return True
