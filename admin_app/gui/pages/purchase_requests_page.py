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
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.approval_queue import ApprovalQueue
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.price_range_form_popup import PriceRangeFormPopup
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.gui.components.styled_table import cell, styled_table
from shared.i18n import enum_label, tr
from admin_app.theme import CLASSICAL_PALETTE
from database import product_repository, purchase_order_repository
from database.exceptions import DATABASE_ERRORS, PurchaseOrderAlreadyDecidedError
from shared.formatting import local_datetime_text
from shared.currency import format_money
from shared.models import PriceRange, Product, PurchaseOrder
from shared import current_session

_STATUS_FILTERS = (
    ("All", None), ("Awaiting approval", "pending"), ("Sent", "sent"), ("Partially received", "partially_received"),
    ("Received", "received"), ("Rejected", "rejected"), ("Cancelled", "cancelled"),
)
_ACTIONS_COLUMN = 10  # the orders table's last column: Cancel on orders still waiting for goods


def order_status_label(order: PurchaseOrder) -> str:
    if order.status == "pending":
        return enum_label("po_status", "Awaiting approval")
    if order.status == "rejected":
        return enum_label("po_status", "Rejected")
    if order.status == "cancelled":
        return enum_label("po_status", "Cancelled")
    if order.status in ("received", "partially_received"):
        label = enum_label("po_status", "Received" if order.status == "received" else "Partially received")
        return tr("admin.purchase.status_progress").format(
            label=label, received=order.received_qty, quantity=order.quantity
        )
    return enum_label("po_status", "Approved · sent" if order.was_approved else "Sent")


def _status_color(order: PurchaseOrder) -> str:
    p = CLASSICAL_PALETTE
    if order.status == "pending":
        return p["accent"]
    if order.status in ("rejected", "cancelled"):
        return p["alert_critical"] if order.status == "rejected" else p["text_secondary"]
    return p["alert_success"]


class PurchaseRequestsPage(AdminPage):
    """Emits pending_count_changed(n) after anything that may change the
    number of held orders, so the sidebar badge can follow immediately
    rather than waiting for the next poll."""

    pending_count_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.purchase_requests.title"), parent, subtitle=tr("page.purchase_requests.subtitle"))

        refresh = CompactButton(tr("admin.refresh"))
        refresh.clicked.connect(self.reload)
        self.add_header_action(refresh)

        self._products: list[Product] = []
        self._ranges: list[PriceRange] = []
        self._orders: list[PurchaseOrder] = []
        # Ids of the held orders as of the last SUCCESSFUL load (None until
        # then, and after a failed one - so the next check always reloads).
        self._pending_ids: list[int] | None = None
        self._load_error: str | None = None
        self._cancel_buttons: dict[int, CompactButton] = {}

        self.body_layout().addWidget(self._build_kpi_row())

        middle = QWidget()
        middle_layout = QHBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(16)

        self._queue_section = Section(tr("admin.purchase.queue_kicker"), tr("admin.purchase.queue_heading"))
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
        self._pending_card = StatCard(tr("admin.purchase.kpi_pending"), "—")
        self._pending_value_item = stat_breakdown_item(tr("admin.purchase.i_value"), "—")
        self._pending_card.footer_layout().addWidget(self._pending_value_item)
        self._sent_card = StatCard(tr("admin.purchase.kpi_sent"), "—")
        self._sent_direct_item = stat_breakdown_item(tr("admin.purchase.i_within"), "0")
        self._sent_approved_item = stat_breakdown_item(tr("admin.purchase.i_approved"), "0")
        self._sent_card.footer_layout().addWidget(self._sent_direct_item)
        self._sent_card.footer_layout().addWidget(self._sent_approved_item)
        self._delivered_card = StatCard(tr("admin.purchase.kpi_delivered"), "—")
        self._delivered_full_item = stat_breakdown_item(tr("admin.purchase.i_full"), "0")
        self._delivered_partial_item = stat_breakdown_item(tr("admin.purchase.i_partial"), "0")
        self._delivered_card.footer_layout().addWidget(self._delivered_full_item)
        self._delivered_card.footer_layout().addWidget(self._delivered_partial_item)
        self._rejected_card = StatCard(tr("admin.purchase.kpi_rejected"), "—")
        self._ranges_item = stat_breakdown_item(tr("admin.purchase.i_ranges"), "0")
        self._rejected_card.footer_layout().addWidget(self._ranges_item)
        for card in (self._pending_card, self._sent_card, self._delivered_card, self._rejected_card):
            layout.addWidget(card, stretch=1)
        return row

    def _build_ranges_section(self) -> Section:
        section = Section(tr("admin.purchase.ranges_kicker"), tr("admin.purchase.ranges_heading"))
        add = CompactButton(tr("admin.purchase.set_range"))
        add.clicked.connect(self._open_add_range)
        section.add_header_control(add)
        self._ranges_table = styled_table([tr(f"admin.purchase.rcol_{key}") for key in ("product", "min", "max", "supplier")])
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
        edit = CompactButton(tr("admin.purchase.edit"))
        edit.clicked.connect(self._open_edit_range)
        remove = CompactButton(tr("admin.purchase.remove"))
        remove.clicked.connect(self._remove_range)
        row.addWidget(edit)
        row.addWidget(remove)
        row.addStretch(1)
        self._ranges_hint = QLabel(tr("admin.purchase.ranges_hint"))
        self._ranges_hint.setStyleSheet(f"font-size: 11px; color: {CLASSICAL_PALETTE['text_secondary']};")
        row.addWidget(self._ranges_hint)
        section.body_layout().addWidget(buttons)
        return section

    def _build_orders_section(self) -> Section:
        section = Section(tr("admin.purchase.hist_kicker"), tr("admin.purchase.hist_heading"))
        self._filter_input = QComboBox()
        p = CLASSICAL_PALETTE
        self._filter_input.setStyleSheet(
            f"QComboBox {{ background-color: {p['surface_raised']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; border-radius: {p['radius_sm']}; padding: 3px 10px; font-size: 12px; }}"
        )
        for label, _status in _STATUS_FILTERS:
            self._filter_input.addItem(enum_label("po_filter", label))
        self._filter_input.currentIndexChanged.connect(self._render_orders)
        section.add_header_control(self._filter_input)
        self._orders_table = styled_table(
            [tr(f"admin.purchase.ocol_{key}") for key in (
                "po", "created", "site", "item", "supplier", "qty", "unit", "total", "band", "status", "actions")]
        )
        header = self._orders_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 1, 5, 6, 7, 8, 9, _ACTIONS_COLUMN):
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
        """Re-read everything. A database failure shows an error state
        (never the "All requests reviewed." empty state, which would tell
        an admin nothing is waiting when really nothing could be read) and
        does not emit pending_count_changed."""
        try:
            products = product_repository.list_all()
            ranges = purchase_order_repository.list_price_ranges()
            orders = purchase_order_repository.list_orders()
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._show_load_error(exc)
            return
        self._products, self._ranges, self._orders = products, ranges, orders
        self._load_error = None

        pending = [o for o in self._orders if o.status == "pending"]
        sent = [o for o in self._orders if o.status == "sent"]
        rejected = [o for o in self._orders if o.status == "rejected"]
        received = [o for o in self._orders if o.status == "received"]
        partial = [o for o in self._orders if o.status == "partially_received"]
        self._pending_ids = sorted(o.id for o in pending)

        self._queue.set_orders(pending)
        held_value = round(sum(o.total for o in pending), 2)
        self._held_total_label.setText(
            tr("admin.purchase.held").format(amount=format_money(held_value)) if pending else ""
        )

        self._pending_card.set_value(str(len(pending)))
        self._pending_value_item.layout().itemAt(1).widget().setText(format_money(held_value))
        self._sent_card.set_value(str(len(sent)))
        self._sent_direct_item.layout().itemAt(1).widget().setText(str(sum(1 for o in sent if not o.was_approved)))
        self._sent_approved_item.layout().itemAt(1).widget().setText(str(sum(1 for o in sent if o.was_approved)))
        self._delivered_card.set_value(str(len(received) + len(partial)))
        self._delivered_full_item.layout().itemAt(1).widget().setText(str(len(received)))
        self._delivered_partial_item.layout().itemAt(1).widget().setText(str(len(partial)))
        self._rejected_card.set_value(str(len(rejected)))
        self._ranges_item.layout().itemAt(1).widget().setText(
            tr("admin.purchase.ranges_of").format(n=len(self._ranges), total=len(self._products))
        )

        self._render_ranges()
        self._render_orders()
        self.pending_count_changed.emit(len(pending))

    def _show_load_error(self, exc: Exception) -> None:
        self._products, self._ranges, self._orders = [], [], []
        self._pending_ids = None
        self._load_error = tr("admin.purchase.load_failed").format(error=exc)
        self._queue.set_error(self._load_error)
        self._held_total_label.setText("")
        for card in (self._pending_card, self._sent_card, self._delivered_card, self._rejected_card):
            card.set_value("—")
        self._pending_value_item.layout().itemAt(1).widget().setText("—")
        self._sent_direct_item.layout().itemAt(1).widget().setText("—")
        self._sent_approved_item.layout().itemAt(1).widget().setText("—")
        self._delivered_full_item.layout().itemAt(1).widget().setText("—")
        self._delivered_partial_item.layout().itemAt(1).widget().setText("—")
        self._ranges_item.layout().itemAt(1).widget().setText("—")
        self._render_ranges()
        self._render_orders()

    def load_error(self) -> str | None:
        """The message shown instead of the queue when the last reload
        failed, else None."""
        return self._load_error

    def reload_if_pending_changed(self) -> bool:
        """Cheap poll hook for the host window: reload when the SET of held
        orders differs from what's showing (a count can stay equal while
        one order is decided elsewhere and another raised). A failed read
        leaves the page as is. Returns whether it reloaded."""
        try:
            ids = purchase_order_repository.pending_ids()
        except (ValueError, *DATABASE_ERRORS):
            return False
        if self._pending_ids is not None and ids == self._pending_ids:
            return False
        self.reload()
        return True

    def pending_orders(self) -> list[PurchaseOrder]:
        return [o for o in self._orders if o.status == "pending"]

    def _render_ranges(self) -> None:
        names = {p.barcode: p.name for p in self._products}
        self._ranges_table.setRowCount(len(self._ranges))
        for row, band in enumerate(self._ranges):
            name = names.get(band.product_barcode, "")
            self._ranges_table.setItem(row, 0, cell(f"{band.product_barcode} · {name}"))
            self._ranges_table.setItem(row, 1, cell(format_money(band.min_unit_price), right=True))
            self._ranges_table.setItem(row, 2, cell(format_money(band.max_unit_price), right=True))
            self._ranges_table.setItem(row, 3, cell(band.default_supplier or "—"))

    def _render_orders(self) -> None:
        status = _STATUS_FILTERS[max(self._filter_input.currentIndex(), 0)][1]
        rows = [o for o in self._orders if status is None or o.status == status]
        self._orders_table.setRowCount(0)  # drops the old rows' Cancel buttons with them
        self._cancel_buttons = {}
        self._orders_table.setRowCount(len(rows))
        for row, order in enumerate(rows):
            band = (
                f"{format_money(order.range_min)}–{format_money(order.range_max)}"
                if order.range_min is not None and order.range_max is not None
                else tr("admin.purchase.band_none")
            )
            values = [
                cell(order.number),
                cell(local_datetime_text(order.created_at)),
                cell(order.site),
                cell(f"{order.product_barcode} · {order.product_name}"),
                cell(order.supplier),
                cell(str(order.quantity), right=True),
                cell(format_money(order.unit_price), right=True),
                cell(format_money(order.total), right=True),
                cell(band),
                cell(order_status_label(order), color=_status_color(order)),
            ]
            # Who raised it, decision time / who decided + reason on hover,
            # rather than more columns.
            tooltip = []
            if order.raised_by:
                tooltip.append(tr("admin.purchase.tt_raised").format(who=order.raised_by))
            if order.decided_at:
                tooltip.append(tr("admin.purchase.tt_decided").format(when=local_datetime_text(order.decided_at))
                               + (tr("admin.purchase.tt_by").format(who=order.decided_by) if order.decided_by else ""))
            if order.decision_note:
                tooltip.append(order.decision_note)
            if order.received_at:
                tooltip.append(tr("admin.purchase.tt_received").format(
                    when=local_datetime_text(order.received_at), who=order.received_by or "—"))
            if order.cancelled_at:
                tooltip.append(tr("admin.purchase.tt_cancelled").format(
                    when=local_datetime_text(order.cancelled_at), who=order.cancelled_by or "—"))
            if tooltip:
                values[9].setToolTip("\n".join(tooltip))
            for column, item in enumerate(values):
                self._orders_table.setItem(row, column, item)
            # Everything past "pending" is read-only here except one thing an
            # administrator may still do: call off an approved order that
            # hasn't been delivered in full.
            if order.status in ("sent", "partially_received"):
                button = CompactButton(tr("admin.purchase.cancel_order"))
                button.setObjectName(f"cancel-{order.id}")
                button.clicked.connect(lambda _checked=False, order_id=order.id: self._cancel(order_id))
                self._cancel_buttons[order.id] = button
                self._orders_table.setCellWidget(row, _ACTIONS_COLUMN, button)

    def cancel_button(self, order_id: int) -> CompactButton | None:
        """The Cancel button on an order's row in the history table (None
        when its row isn't shown or the order can't be cancelled here)."""
        return self._cancel_buttons.get(order_id)

    # --- approvals ---------------------------------------------------------

    def _ask_reject_note(self, order_number: str) -> tuple[str, bool]:
        """Optional reason shown back to the depot. Separate method so
        tests can answer it without a modal dialog."""
        return QInputDialog.getText(
            self, tr("admin.purchase.reject_title").format(number=order_number), tr("admin.purchase.reject_prompt")
        )

    def _confirm_approve(self, order: PurchaseOrder | None, order_id: int) -> bool:
        """Approving releases the order to the supplier, so ask first,
        naming the supplier and total. Separate method so tests can answer
        it without a modal dialog."""
        if order is None:
            text = tr("admin.purchase.approve_q_plain").format(id=f"{order_id:05d}")
        else:
            text = tr("admin.purchase.approve_q").format(
                number=order.number, supplier=order.supplier, total=format_money(order.total),
                qty=order.quantity, unit=format_money(order.unit_price),
            )
        answer = QMessageBox.question(self, tr("admin.purchase.approve_title"), text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _approve(self, order_id: int) -> None:
        order = next((o for o in self._orders if o.id == order_id), None)
        if not self._confirm_approve(order, order_id):
            return
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
                message = tr("admin.purchase.msg_approved").format(
                    number=order.number, total=format_money(order.total), supplier=order.supplier
                )
            else:
                order = purchase_order_repository.reject(order_id, note, decided_by=current_session.actor())
                message = tr("admin.purchase.msg_rejected").format(number=order.number, total=format_money(order.total))
        except PurchaseOrderAlreadyDecidedError as exc:
            message = tr("admin.purchase.msg_decided").format(error=exc)
        except (ValueError, *DATABASE_ERRORS) as exc:
            message = tr("admin.purchase.msg_save_failed").format(error=exc)
        self.reload()
        self._queue.set_last_action(message)

    # --- cancelling an approved order ----------------------------------------

    def _confirm_cancel(self, order: PurchaseOrder) -> bool:
        """Cancelling is final (no more goods can be received against the
        order), so ask first and say what happens to what already arrived.
        Separate method so tests can answer it without a modal dialog."""
        if order.received_qty:
            text = tr("admin.purchase.cancel_q_partial").format(
                number=order.number, supplier=order.supplier, qty=order.quantity,
                received=order.received_qty, remaining=order.quantity - order.received_qty,
            )
        else:
            text = tr("admin.purchase.cancel_q").format(
                number=order.number, supplier=order.supplier, qty=order.quantity, unit=format_money(order.unit_price),
            )
        answer = QMessageBox.question(
            self, tr("admin.purchase.cancel_title"), text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        return answer == QMessageBox.Yes

    def _cancel(self, order_id: int) -> None:
        order = next((o for o in self._orders if o.id == order_id), None)
        if order is None or not self._confirm_cancel(order):
            return
        try:
            done = purchase_order_repository.cancel_order(order_id, current_session.actor())
            message = tr("admin.purchase.msg_cancelled").format(number=done.number, total=format_money(done.total))
        except (ValueError, *DATABASE_ERRORS) as exc:
            message = tr("admin.purchase.msg_cancel_failed").format(error=exc)
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
            self._ranges_hint.setText(tr("admin.purchase.select_range"))
            return
        self._range_popup.open_or_refresh(products=self._products, price_range=band)

    def _save_range(self) -> None:
        try:
            purchase_order_repository.set_price_range(self._range_popup.result_range())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._ranges_hint.setText(tr("admin.purchase.msg_save_failed").format(error=exc))
            return
        self._ranges_hint.setText(tr("admin.purchase.range_saved"))
        self.reload()

    def _remove_range(self) -> None:
        band = self._selected_range()
        if band is None:
            self._ranges_hint.setText(tr("admin.purchase.select_range"))
            return
        try:
            purchase_order_repository.delete_price_range(band.product_barcode)
        except DATABASE_ERRORS as exc:
            self._ranges_hint.setText(tr("admin.purchase.remove_failed").format(error=exc))
            return
        self._ranges_hint.setText(tr("admin.purchase.range_removed").format(barcode=band.product_barcode))
        self.reload()
