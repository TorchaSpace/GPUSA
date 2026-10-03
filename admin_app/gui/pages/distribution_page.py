"""Distribution page - the real build of Distribution.dc.html
("Distribution Network"), replacing its themed placeholder.

Recreated from the mockup:
- KPI cards: In transit (items, shipments on the road, origins),
  Deliveries today (N of M completed, with a bar), Delayed shipments
  (count, average lateness).
- "Route tracker · Active routes, warehouse to dealership"
  (components/route_tracker.py) - click a route to select it.
- "Live Transit Ledger" - Tracking ID / Origin / Destination / Carrier /
  Items / Status / ETA (with the "+2h 50m" late note), the status
  filter, and row selection synced with the route tracker.

All real, from database.shipment_repository (the depot's Console >
Shipments creates and dispatches; each dealership's POS receives), with
every status and number from shared.distribution.

Added: "Delivered · last 7 days" - what arrived, and any discrepancy a
dealership reported (e.g. "BOX-2218 −4 · '4 cartons crushed'"), since
that's the part of distribution an admin actually has to act on.

Deliberately different from the mockup: its "Carrier feeds refresh every
30 s" footer and drifting progress bars imply live GPS that doesn't
exist here - this page says progress is estimated from departure and
ETA. Its fabricated context lines ("Weather on I-81 · 1 carrier
breakdown", the hardcoded 1,204 / 41 of 57 figures) are replaced by the
real counts and, for delays, the carriers involved.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.route_tracker import GOLD, STATUS_COLORS, WARN, RouteTracker
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard
from admin_app.gui.components.styled_table import cell, styled_table
from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE
from database import shipment_repository
from shared.constants import SHIPMENT_POLL_INTERVAL_MS
from shared.distribution import duration_text, eta_text, lateness, live_status, summarize
from shared.formatting import local_datetime_text, parse_db_timestamp
from shared.gui_kit.polling import PollingTimer
from shared.models import Shipment

_FILTERS = ("All", "Scheduled", "In Transit", "Arriving", "Delayed")
_DELIVERED_WINDOW = timedelta(days=7)


class _Segment(QPushButton):
    def __init__(self, label: str, parent=None):
        super().__init__(label, parent)
        p = CLASSICAL_PALETTE
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; color: {p['text_secondary']}; border: none;
                border-bottom: 2px solid transparent; padding: 6px 10px; font-size: 13px; }}
            QPushButton:hover {{ color: {p['text_primary']}; }}
            QPushButton:checked {{ color: {p['accent']}; border-bottom: 2px solid {p['accent']}; }}
            """
        )


class DistributionPage(AdminPage):
    def __init__(self, parent: QWidget | None = None, now_provider=None):
        super().__init__(tr("page.distribution.title"), parent, subtitle=tr("page.distribution.subtitle"))
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._shipments: list[Shipment] = []
        self._active: list[Shipment] = []
        self._shown: list[Shipment] = []
        self._selected_id: int | None = None
        self._filter = "All"

        refresh = CompactButton(tr("admin.refresh"))
        refresh.clicked.connect(self.reload)
        self.add_header_action(refresh)

        self.body_layout().addWidget(self._build_kpis())

        self._routes_section = Section("Route tracker", "Active routes, warehouse to dealership")
        p = CLASSICAL_PALETTE
        legend = QLabel(
            f"<span style='color:{GOLD}'>━</span> In transit &nbsp; <span style='color:#eae7e7'>━</span> Arriving "
            f"&nbsp; <span style='color:{WARN}'>━</span> Delayed &nbsp; <span style='color:{p['border']}'>┅</span> Remaining"
        )
        legend.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        self._routes_section.add_header_control(legend)
        self._tracker = RouteTracker()
        self._tracker.route_clicked.connect(self._toggle_select)
        self._routes_section.body_layout().addWidget(self._tracker)
        self.body_layout().addWidget(self._routes_section)

        self.body_layout().addWidget(self._build_ledger())
        self.body_layout().addWidget(self._build_delivered())

        self._poller = PollingTimer(self._fetch, interval_ms=SHIPMENT_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_fetched)
        self.reload()

    def now(self) -> datetime:
        return self._now_provider()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._poller.start()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    # --- KPIs ------------------------------------------------------------

    def _build_kpis(self) -> QWidget:
        p = CLASSICAL_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._transit_card = StatCard("In transit", "—")
        self._transit_note = QLabel()
        self._transit_card.footer_layout().addWidget(self._transit_note)

        self._today_card = StatCard("Deliveries today", "—")
        self._today_bar = QProgressBar()
        self._today_bar.setTextVisible(False)
        self._today_bar.setFixedHeight(4)
        self._today_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {p['border']}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background-color: {GOLD}; border-radius: 2px; }}"
        )
        self._today_note = QLabel()
        self._today_card.footer_layout().addWidget(self._today_note)

        self._delayed_card = StatCard("Delayed shipments", "—")
        self._delayed_note = QLabel()
        self._delayed_card.footer_layout().addWidget(self._delayed_note)

        for label in (self._transit_note, self._today_note, self._delayed_note):
            label.setWordWrap(True)
            label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        # the bar sits between the value and the footer, like the mockup
        self._today_card.layout().insertWidget(2, self._today_bar)
        for card in (self._transit_card, self._today_card, self._delayed_card):
            layout.addWidget(card, stretch=1)
        return row

    # --- ledger ------------------------------------------------------------

    def _build_ledger(self) -> Section:
        p = CLASSICAL_PALETTE
        section = Section("Live", "Live Transit Ledger")
        self._filter_group = QButtonGroup(self)
        self._filter_buttons: dict[str, _Segment] = {}
        for name in _FILTERS:
            button = _Segment(name)
            button.clicked.connect(lambda _c=False, n=name: self.set_filter(n))
            self._filter_group.addButton(button)
            self._filter_buttons[name] = button
            section.add_header_control(button)
        self._filter_buttons["All"].setChecked(True)

        self._table = styled_table(["Tracking ID", "Origin (warehouse)", "Destination (dealership)", "Carrier", "Items", "Status", "ETA"])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setMinimumHeight(240)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        section.body_layout().addWidget(self._table)

        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 8, 16, 10)
        self._ledger_count = QLabel()
        honest = QLabel("Progress and ETAs come from each shipment's schedule - there's no live carrier tracking.")
        for label in (self._ledger_count, honest):
            label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        row.addWidget(self._ledger_count)
        row.addStretch(1)
        row.addWidget(honest)
        section.body_layout().addWidget(footer)
        return section

    def _build_delivered(self) -> Section:
        section = Section("Delivered · last 7 days", "Receipts & discrepancies")
        self._delivered_table = styled_table(["Tracking ID", "Destination", "Delivered", "Items", "Late", "Discrepancies"])
        header = self._delivered_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self._delivered_table.setMinimumHeight(160)
        section.body_layout().addWidget(self._delivered_table)
        return section

    # --- data --------------------------------------------------------------

    def _fetch(self) -> list[Shipment] | None:
        try:
            return shipment_repository.list_shipments()
        except Exception:  # a transient lock on a timer tick
            return None

    def reload(self) -> None:
        self._on_fetched(self._fetch())

    def _on_fetched(self, shipments: list[Shipment] | None) -> None:
        if shipments is None:
            return
        now = self.now()
        p = CLASSICAL_PALETTE
        self._shipments = shipments
        self._active = [s for s in shipments if s.is_active]

        summary = summarize(shipments, now)
        self._transit_card.set_value(f"{summary.in_transit_items:,}")
        origins = len(summary.origins)
        self._transit_note.setText(
            f"items · {summary.in_transit_shipments} shipment{'s' if summary.in_transit_shipments != 1 else ''} on the road"
            f" · {origins} origin{'s' if origins != 1 else ''}"
        )
        self._today_card.set_value(str(summary.delivered_today))
        self._today_bar.setMaximum(max(summary.due_today, 1))
        self._today_bar.setValue(summary.delivered_today)
        self._today_note.setText(f"of {summary.due_today} due today completed")
        self._delayed_card.set_value(str(len(summary.delayed)))
        if summary.delayed:
            carriers = Counter(s.carrier for s in summary.delayed)
            carrier_text = " · ".join(f"{name} {count}" if count > 1 else name for name, count in carriers.most_common(3))
            self._delayed_note.setText(f"avg. {duration_text(summary.average_delay).lstrip('+')} late · {carrier_text}")
            self._delayed_note.setStyleSheet(f"font-size: 12px; color: {WARN};")
        else:
            self._delayed_note.setText("Everything on schedule")
            self._delayed_note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")

        if self._selected_id not in {s.id for s in self._active}:
            self._selected_id = None
        self._render_routes()
        self._render_table()
        self._render_delivered()

    def set_filter(self, name: str) -> None:
        self._filter = name
        self._filter_buttons[name].setChecked(True)
        self._render_table()

    def _toggle_select(self, shipment_id: int) -> None:
        self._selected_id = None if self._selected_id == shipment_id else shipment_id
        self._render_routes()
        self._sync_table_selection()

    def selected_id(self) -> int | None:
        return self._selected_id

    def _render_routes(self) -> None:
        now_local = self.now().astimezone()
        self._tracker.set_routes(self._active, self._selected_id, f"Now · {now_local.strftime('%H:%M')}")

    def _render_table(self) -> None:
        now = self.now()
        rows = [s for s in self._active if self._filter == "All" or live_status(s, now) == self._filter]
        self._shown = rows
        self._table.blockSignals(True)
        self._table.setRowCount(len(rows))
        for index, shipment in enumerate(rows):
            status = live_status(shipment, now)
            late = lateness(shipment, now)
            eta = eta_text(shipment, now) + (f" ({duration_text(late)})" if status == "Delayed" else "")
            values = [
                cell(shipment.number, color=GOLD),
                cell(shipment.origin),
                cell(f"{shipment.dealership_name} · {shipment.dealership_code}"),
                cell(shipment.carrier),
                cell(str(shipment.item_count), right=True),
                cell(f"● {status}", color=STATUS_COLORS.get(status, GOLD)),
                cell(eta, right=True, color=WARN if status == "Delayed" else None),
            ]
            for column, item in enumerate(values):
                self._table.setItem(index, column, item)
        self._table.blockSignals(False)
        noun = "shipment" if len(rows) == 1 else "shipments"
        self._ledger_count.setText(f"{len(rows)} {noun} · select a row to trace its route")
        self._sync_table_selection()

    def _sync_table_selection(self) -> None:
        self._table.blockSignals(True)
        self._table.clearSelection()
        for index, shipment in enumerate(self._shown):
            if shipment.id == self._selected_id:
                self._table.selectRow(index)
        self._table.blockSignals(False)

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        self._selected_id = self._shown[rows[0].row()].id
        self._render_routes()

    def _render_delivered(self) -> None:
        now = self.now()
        cutoff = now - _DELIVERED_WINDOW
        delivered = [
            s for s in self._shipments
            if s.status == "delivered" and s.delivered_at and parse_db_timestamp(s.delivered_at) >= cutoff
        ]
        delivered.sort(key=lambda s: s.delivered_at, reverse=True)
        self._delivered_table.setRowCount(len(delivered))
        for index, shipment in enumerate(delivered):
            late = lateness(shipment, now)
            issues = ", ".join(f"{l.product_barcode} {l.discrepancy:+d}" for l in shipment.discrepancies)
            if shipment.receipt_note:
                issues = f"{issues} · “{shipment.receipt_note}”" if issues else f"“{shipment.receipt_note}”"
            values = [
                cell(shipment.number, color=GOLD),
                cell(f"{shipment.dealership_name} · {shipment.dealership_code}"),
                cell(local_datetime_text(shipment.delivered_at)),
                cell(str(shipment.item_count), right=True),
                cell(duration_text(late) if late.total_seconds() >= 60 else "on time",
                     color=WARN if late.total_seconds() >= 900 else None),
                cell(issues or "none", color=WARN if shipment.discrepancies else CLASSICAL_PALETTE["text_secondary"]),
            ]
            for column, item in enumerate(values):
                self._delivered_table.setItem(index, column, item)
