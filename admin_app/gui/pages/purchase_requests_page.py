"""Purchase Requests page - the admin side of the Purchase Requests /
Purchase Orders domain, replacing its themed placeholder.

The admin mockup has no page of its own for this (its sidebar link is
href="#"); the pieces come from where the mockup does show them:
- "Admin action required · Out-of-range purchase requests" with
  Approve / Reject (Inventory Dashboard.dc.html's approvals dropdown) ->
  the approval queue here (components/approval_queue.py).
- Settings > Pricing Thresholds > "Safe Purchase Price Range" -> the
  "Safe price ranges" section here. Kept on this page rather than a
  Settings page because Settings doesn't exist yet and a band is only
  meaningful next to the orders it governs. Per product, not per
  category: products have no category field.
- "View all requests →" -> the "All purchase orders" table with a status
  filter.

All real, through database.purchase_order_repository. Dropped from the
Settings mockup's approval rules, deliberately: per-role approval
("Operations Admin" / site-manager tolerance %), escalation timers and
notification toggles - there's no login or roles system for any of
them to act on, so they'd be switches that do nothing. Every held order
waits for an admin here; out-of-band prices in both directions are held
(the depot mockup's behavior).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.approval_queue import ApprovalQueue
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.price_range_form_popup import PriceRangeFormPopup
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.gui.components.styled_table import cell, styled_table
from admin_app.theme import CLASSICAL_PALETTE
from database import product_repository, purchase_order_repository
from database.exceptions import DataAccessError, PurchaseOrderAlreadyDecidedError
from shared.formatting import format_amount, local_datetime_text
from shared.models import PriceRange, Product, PurchaseOrder
from shared import current_session

_STATUS_FILTERS = (("All", None), ("Awaiting approval", "pending"), ("Sent", "sent"), ("Rejected", "rejected"))


def order_status_label(order: PurchaseOrder) -> str:
    if order.status == "pending":
        return "Awaiting approval"
    if order.status == "rejected":
        return "Rejected"
    return "Approved · sent" if order.was_approved else "Sent"


def _status_color(order: PurchaseOrder) -> str:
    p = CLASSICAL_PALETTE
    if order.status == "pending":
        return p["accent"]
    if order.status == "rejected":
        return p["alert_critical"]
    return p["alert_success"]


class PurchaseRequestsPage(AdminPage):
    """Emits pending_count_changed(n) after anything that may change the
    number of held orders, so the sidebar badge can follow immediately
    rather than waiting for the next poll."""

    pending_count_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Purchase Requests", parent)

        refresh = CompactButton("Refresh")
        refresh.clicked.connect(self.reload)
        self.add_header_action(refresh)

        self._products: list[Product] = []
        self._ranges: list[PriceRange] = []
        self._orders: list[PurchaseOrder] = []

        self.body_layout().addWidget(self._build_kpi_row())

        middle = QWidget()
        middle_layout = QHBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(16)

        self._queue_section = Section("Admin action required", "Out-of-range purchase requests")
        self._held_total_label = QLabel()
        self._held_total_label.setStyleSheet(f"font-size: 11px; color: {CLASSICAL_PALETTE['text_secondary']};")
        self._queue_section.add_header_control(self._held_total_label)
        self._queue = ApprovalQueue()
        self._queue.approve_requested.connect(self._approve)
        self._queue.reject_requested.connect(self._reject)
        self._queue_section.body_layout().addWidget(self._queue)
        middle_layout.addWidget(self._queue_section, stretch=3, alignment=Qt.AlignTop)

        middle_layout.addWidget(self._build_ranges_section(), stretch=2, alignment=Qt.AlignTop)
        self.body_layout().addWidget(middle)

        self.body_layout().addWidget(self._build_orders_section(), stretch=1)

        self._range_popup = PriceRangeFormPopup(self)
        self._range_popup.accepted.connect(self._save_range)

        self.reload()

    # --- building ----------------------------------------------------------

    def _build_kpi_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self._pending_card = StatCard("Awaiting approval", "—")
        self._pending_value_item = stat_breakdown_item("Value held", "—")
        self._pending_card.footer_layout().addWidget(self._pending_value_item)
        self._sent_card = StatCard("Sent to suppliers", "—")
        self._sent_direct_item = stat_breakdown_item("Within band", "0")
        self._sent_approved_item = stat_breakdown_item("Approved by admin", "0")
        self._sent_card.footer_layout().addWidget(self._sent_direct_item)
        self._sent_card.footer_layout().addWidget(self._sent_approved_item)
        self._rejected_card = StatCard("Rejected", "—")
        self._ranges_item = stat_breakdown_item("Products with a safe band", "0")
        self._rejected_card.footer_layout().addWidget(self._ranges_item)
        for card in (self._pending_card, self._sent_card, self._rejected_card):
            layout.addWidget(card, stretch=1)
        return row

    def _build_ranges_section(self) -> Section:
        section = Section("Pricing thresholds", "Safe price ranges")
        add = CompactButton("Set range")
        add.clicked.connect(self._open_add_range)
        section.add_header_control(add)
        self._ranges_table = styled_table(["Product", "Min / unit", "Max / unit", "Default supplier"])
        header = self._ranges_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._ranges_table.setMinimumHeight(220)
        self._ranges_table.doubleClicked.connect(lambda _i: self._open_edit_range())
        section.body_layout().addWidget(self._ranges_table)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(12, 8, 12, 10)
        edit = CompactButton("Edit")
        edit.clicked.connect(self._open_edit_range)
        remove = CompactButton("Remove")
        remove.clicked.connect(self._remove_range)
        row.addWidget(edit)
        row.addWidget(remove)
        row.addStretch(1)
        self._ranges_hint = QLabel("Products without a band: every order is held.")
        self._ranges_hint.setStyleSheet(f"font-size: 11px; color: {CLASSICAL_PALETTE['text_secondary']};")
        row.addWidget(self._ranges_hint)
        section.body_layout().addWidget(buttons)
        return section

    def _build_orders_section(self) -> Section:
        section = Section("History", "All purchase orders")
        self._filter_input = QComboBox()
        p = CLASSICAL_PALETTE
        self._filter_input.setStyleSheet(
            f"QComboBox {{ background-color: {p['surface_raised']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; border-radius: {p['radius_sm']}; padding: 3px 10px; font-size: 12px; }}"
        )
        for label, _status in _STATUS_FILTERS:
            self._filter_input.addItem(label)
        self._filter_input.currentIndexChanged.connect(self._render_orders)
        section.add_header_control(self._filter_input)
        self._orders_table = styled_table(
            ["PO", "Created", "Site", "Item", "Supplier", "Qty", "Unit", "Total", "Safe band", "Status"]
        )
        header = self._orders_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 1, 5, 6, 7, 8, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        for column in (2, 4):  # site, supplier: long free text - fixed width, elided
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self._orders_table.setColumnWidth(column, 130)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setMinimumSectionSize(60)
        self._orders_table.setMinimumHeight(320)
        section.body_layout().addWidget(self._orders_table)
        return section

    # --- data --------------------------------------------------------------

    def reload(self) -> None:
        try:
            self._products = product_repository.list_all()
            self._ranges = purchase_order_repository.list_price_ranges()
            self._orders = purchase_order_repository.list_orders()
        except DataAccessError:
            self._products, self._ranges, self._orders = [], [], []

        pending = [o for o in self._orders if o.status == "pending"]
        sent = [o for o in self._orders if o.status == "sent"]
        rejected = [o for o in self._orders if o.status == "rejected"]

        self._queue.set_orders(pending)
        held_value = sum(o.total for o in pending)
        self._held_total_label.setText(f"{format_amount(held_value)} held" if pending else "")

        self._pending_card.set_value(str(len(pending)))
        self._pending_value_item.layout().itemAt(1).widget().setText(format_amount(held_value))
        self._sent_card.set_value(str(len(sent)))
        self._sent_direct_item.layout().itemAt(1).widget().setText(str(sum(1 for o in sent if not o.was_approved)))
        self._sent_approved_item.layout().itemAt(1).widget().setText(str(sum(1 for o in sent if o.was_approved)))
        self._rejected_card.set_value(str(len(rejected)))
        self._ranges_item.layout().itemAt(1).widget().setText(f"{len(self._ranges)} of {len(self._products)}")

        self._render_ranges()
        self._render_orders()
        self.pending_count_changed.emit(len(pending))

    def pending_orders(self) -> list[PurchaseOrder]:
        return [o for o in self._orders if o.status == "pending"]

    def _render_ranges(self) -> None:
        names = {p.barcode: p.name for p in self._products}
        self._ranges_table.setRowCount(len(self._ranges))
        for row, band in enumerate(self._ranges):
            name = names.get(band.product_barcode, "")
            self._ranges_table.setItem(row, 0, cell(f"{band.product_barcode} · {name}"))
            self._ranges_table.setItem(row, 1, cell(format_amount(band.min_unit_price), right=True))
            self._ranges_table.setItem(row, 2, cell(format_amount(band.max_unit_price), right=True))
            self._ranges_table.setItem(row, 3, cell(band.default_supplier or "—"))

    def _render_orders(self) -> None:
        status = _STATUS_FILTERS[max(self._filter_input.currentIndex(), 0)][1]
        rows = [o for o in self._orders if status is None or o.status == status]
        self._orders_table.setRowCount(len(rows))
        for row, order in enumerate(rows):
            band = (
                f"{format_amount(order.range_min)}–{format_amount(order.range_max)}"
                if order.range_min is not None and order.range_max is not None
                else "none set"
            )
            values = [
                cell(order.number),
                cell(local_datetime_text(order.created_at)),
                cell(order.site),
                cell(f"{order.product_barcode} · {order.product_name}"),
                cell(order.supplier),
                cell(str(order.quantity), right=True),
                cell(format_amount(order.unit_price), right=True),
                cell(format_amount(order.total), right=True),
                cell(band),
                cell(order_status_label(order), color=_status_color(order)),
            ]
            # Who raised it, decision time / who decided + reason on hover,
            # rather than more columns.
            tooltip = []
            if order.raised_by:
                tooltip.append(f"Raised by {order.raised_by}")
            if order.decided_at:
                tooltip.append(f"Decided {local_datetime_text(order.decided_at)}"
                               + (f" by {order.decided_by}" if order.decided_by else ""))
            if order.decision_note:
                tooltip.append(order.decision_note)
            if tooltip:
                values[9].setToolTip("\n".join(tooltip))
            for column, item in enumerate(values):
                self._orders_table.setItem(row, column, item)

    # --- approvals ---------------------------------------------------------

    def _ask_reject_note(self, order_number: str) -> tuple[str, bool]:
        """Optional reason shown back to the depot. Separate method so
        tests can answer it without a modal dialog."""
        return QInputDialog.getText(self, f"Reject {order_number}", "Reason (optional, shown to the depot):")

    def _approve(self, order_id: int) -> None:
        self._decide(order_id, approve=True, note=None)

    def _reject(self, order_id: int) -> None:
        order = next((o for o in self._orders if o.id == order_id), None)
        number = order.number if order else f"PO-{order_id:05d}"
        note, ok = self._ask_reject_note(number)
        if not ok:
            return
        self._decide(order_id, approve=False, note=note)

    def _decide(self, order_id: int, approve: bool, note: str | None) -> None:
        try:
            if approve:
                order = purchase_order_repository.approve(order_id, note, decided_by=current_session.actor())
                message = f"{order.number} approved · {format_amount(order.total)} · sent to {order.supplier}"
            else:
                order = purchase_order_repository.reject(order_id, note, decided_by=current_session.actor())
                message = f"{order.number} rejected · {format_amount(order.total)}"
        except PurchaseOrderAlreadyDecidedError as exc:
            message = f"{exc} - someone else decided it first; the list has been refreshed."
        except DataAccessError as exc:
            message = f"Couldn't save: {exc}"
        self.reload()
        self._queue.set_last_action(message)

    # --- price ranges ------------------------------------------------------

    def _selected_range(self) -> PriceRange | None:
        rows = self._ranges_table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._ranges[rows[0].row()]

    def _open_add_range(self) -> None:
        with_band = {r.product_barcode for r in self._ranges}
        candidates = [p for p in self._products if p.barcode not in with_band] or self._products
        self._range_popup.open_or_refresh(products=candidates, price_range=None)

    def _open_edit_range(self) -> None:
        band = self._selected_range()
        if band is None:
            self._ranges_hint.setText("Select a range in the table first.")
            return
        self._range_popup.open_or_refresh(products=self._products, price_range=band)

    def _save_range(self) -> None:
        try:
            purchase_order_repository.set_price_range(self._range_popup.result_range())
        except (DataAccessError, ValueError) as exc:
            self._ranges_hint.setText(f"Couldn't save: {exc}")
            return
        self._ranges_hint.setText("Saved. New depot orders are checked against it immediately.")
        self.reload()

    def _remove_range(self) -> None:
        band = self._selected_range()
        if band is None:
            self._ranges_hint.setText("Select a range in the table first.")
            return
        try:
            purchase_order_repository.delete_price_range(band.product_barcode)
        except DataAccessError as exc:
            self._ranges_hint.setText(f"Couldn't remove: {exc}")
            return
        self._ranges_hint.setText(f"Removed. Orders for {band.product_barcode} will now be held.")
        self.reload()
