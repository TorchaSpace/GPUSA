"""The "Receive delivery" dialog: how many units of an approved purchase
order arrived, booked into this depot's warehouse.

A pure form (no database import): the Purchasing panel reads quantity()
back and calls purchase_order_repository.receive_against_order() itself.
The quantity can't go below 1 or above what the order still has due - the
repository refuses an over-receipt anyway, this just makes it impossible
to type one.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QSpinBox, QWidget

from shared.formatting import format_int
from shared.i18n import tr
from shared.models import PurchaseOrder


class ReceiveDeliveryDialog(QDialog):
    def __init__(self, order: PurchaseOrder, warehouse_label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._order = order
        self.setWindowTitle(tr("depot.po.receive_title").format(number=order.number))
        self.setModal(True)

        summary = QLabel(tr("depot.po.receive_summary").format(
            product=f"{order.product_barcode} · {order.product_name}", supplier=order.supplier))
        summary.setWordWrap(True)
        summary.setObjectName("receiveSummary")
        progress = QLabel(tr("depot.po.receive_progress").format(
            quantity=format_int(order.quantity), received=format_int(order.received_qty),
            remaining=format_int(order.remaining_qty)))
        progress.setObjectName("receiveProgress")
        destination = QLabel(tr("depot.po.receive_into").format(warehouse=warehouse_label))
        destination.setObjectName("receiveDestination")

        self._quantity_input = QSpinBox()
        self._quantity_input.setRange(1, max(order.remaining_qty, 1))
        self._quantity_input.setValue(max(order.remaining_qty, 1))  # the usual case: all of what is left
        self._quantity_input.setMinimumHeight(40)

        form = QFormLayout(self)
        form.addRow(summary)
        form.addRow(progress)
        form.addRow(destination)
        form.addRow(tr("depot.po.receive_qty"), self._quantity_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(tr("depot.po.receive_confirm"))
        buttons.button(QDialogButtonBox.Cancel).setText(tr("common.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def quantity(self) -> int:
        return self._quantity_input.value()
