"""Console > Dashboard - this warehouse at a glance, replacing the page's
themed placeholder. The Warehouse Console mockup lists "Dashboard" but
never drew it, so it's built in the Console's Industry style from real
data only:

- Eight stat cells: capacity used (units vs. the capacity set in Admin),
  SKUs held, products below their reorder level HERE, today's units in /
  out, shipments leaving (on the road / scheduled / delayed), staff on the
  floor (checked in / rostered for this warehouse), purchase orders held
  for approval, and overdue ledger documents for this site.
- Three panels: "Below reorder here", "Leaving soon" (the next active
  shipments from here) and "Latest activity" (this warehouse's newest
  stock movements).

Everything is scoped to THIS depot's warehouse (stock levels, shipments
by origin, employees whose Location matches it, POs and ledger entries
by site). Re-read every 15 s while visible.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QHeaderView, QVBoxLayout, QWidget

from database import (
    attendance_repository,
    ledger_repository,
    purchase_order_repository,
    shipment_repository,
    stock_repository,
    warehouse_repository,
)
from depot_app.gui.components.industry_widgets import AMBER, Card, StatCell, industry_table, item
from shared.constants import WAREHOUSE_POLL_INTERVAL_MS
from shared.distribution import eta_text, live_status
from shared.formatting import local_time_text
from shared.gui_kit.polling import PollingTimer
from shared.models import Warehouse
from shared.treasury import is_overdue
from shared.warehousing import (
    STATUS_NEAR,
    capacity_fraction,
    capacity_status,
    direction_label,
    percent_text,
    reason_label,
    start_of_today_db,
    works_at,
)

_LIST_ROWS = 8


def fetch_dashboard(warehouse: Warehouse, today: date | None = None) -> dict:
    """Everything the page shows, in one read (also used by tests)."""
    today = today or date.today()
    try:
        warehouse = warehouse_repository.get_by_code(warehouse.code)  # capacity may have changed in Admin
    except Exception:
        pass
    loc = warehouse.location
    shipments = [
        s for s in shipment_repository.list_shipments(statuses=("scheduled", "in_transit"))
        if s.origin_code == warehouse.code or (s.origin_code is None and s.origin == warehouse.site_label)
    ]
    roster = [r for r in attendance_repository.list_roster()
              if r["is_active"] and works_at(r["location_type"], r["location_name"], warehouse)]
    return {
        "warehouse": warehouse,
        "used": stock_repository.units_by_location().get(loc, 0),
        "skus": len(stock_repository.levels_at(loc)),
        "critical": stock_repository.critical_at(loc),
        "today": stock_repository.movement_totals_since(start_of_today_db(today)).get(loc, (0, 0)),
        "shipments": shipments,
        "on_floor": [r for r in roster if r["status"] == "Present"],
        "rostered": roster,
        "held_orders": purchase_order_repository.list_orders(status="pending", site=warehouse.site_label),
        "overdue": [e for e in ledger_repository.list_entries(site=warehouse.site_label) if is_overdue(e, today)],
        "recent": stock_repository.list_movements(_LIST_ROWS, location=loc),
    }


class DashboardPage(QWidget):
    def __init__(self, warehouse: Warehouse, parent: QWidget | None = None):
        super().__init__(parent)
        self.warehouse = warehouse
        self.data: dict | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cells: dict[str, StatCell] = {}
        for index, (key, caption) in enumerate((
            ("capacity", "Capacity used"), ("skus", "SKUs held"), ("low", "Below reorder here"),
            ("today", "Today in / out"), ("shipments", "Shipments leaving"), ("staff", "On the floor"),
            ("orders", "POs awaiting approval"), ("ledger", "Overdue documents"),
        )):
            cell = StatCell(caption)
            self.cells[key] = cell
            grid.addWidget(cell, index // 4, index % 4)
        layout.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.low_card, self.low_table = self._card("Below reorder here", ["SKU", "Product", "On hand", "Reorder at"], 1)
        self.ship_card, self.ship_table = self._card("Leaving soon", ["No.", "To", "Status", "ETA"], 1)
        self.recent_card, self.recent_table = self._card("Latest activity", ["Time", "Movement", "SKU", "Qty", "Why"], 4)
        for card in (self.low_card, self.ship_card, self.recent_card):
            row.addWidget(card, stretch=1)
        layout.addLayout(row, stretch=1)

        self._poller = PollingTimer(self._fetch, interval_ms=WAREHOUSE_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._render)

    @staticmethod
    def _card(title: str, headers: list[str], stretch_column: int):
        card = Card(title)
        table = industry_table(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.Stretch)
        card.body.addWidget(table, stretch=1)
        return card, table

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()
        self._poller.start()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    def _fetch(self) -> dict | None:
        try:
            return fetch_dashboard(self.warehouse)
        except Exception:  # a transient lock on a timer tick: keep what's shown
            return None

    def reload(self) -> None:
        self._render(self._fetch())

    def _render(self, data: dict | None) -> None:
        if data is None:
            return
        self.data = data
        w = data["warehouse"]
        fraction = capacity_fraction(data["used"], w.capacity_units)
        near = capacity_status(w, data["used"]) == STATUS_NEAR
        if w.capacity_units:
            self.cells["capacity"].set(percent_text(fraction), f"{data['used']:,} of {w.capacity_units:,} units", warn=near)
        else:
            self.cells["capacity"].set(f"{data['used']:,}", "units held · capacity not set in Admin")
        self.cells["skus"].set(str(data["skus"]), "products with stock here")
        out = sum(1 for p in data["critical"] if p.stock_quantity <= 0)
        self.cells["low"].set(str(len(data["critical"])), f"{out} out of stock here", warn=bool(data["critical"]))
        inbound, outbound = data["today"]
        self.cells["today"].set(f"+{inbound:,} / −{outbound:,}", "units since midnight")
        statuses = [live_status(s) for s in data["shipments"]]
        on_road = sum(1 for s in data["shipments"] if s.status == "in_transit")
        delayed = statuses.count("Delayed")
        self.cells["shipments"].set(str(len(data["shipments"])),
                                    f"{on_road} on the road · {len(data['shipments']) - on_road} scheduled"
                                    + (f" · {delayed} delayed" if delayed else ""), warn=bool(delayed))
        self.cells["staff"].set(f"{len(data['on_floor'])} / {len(data['rostered'])}", "checked in / rostered here")
        self.cells["orders"].set(str(len(data["held_orders"])), "held for Admin's decision", warn=bool(data["held_orders"]))
        self.cells["ledger"].set(str(len(data["overdue"])), "checks / notes / invoices past due", warn=bool(data["overdue"]))

        low = data["critical"][:_LIST_ROWS * 2]
        self.low_table.setRowCount(len(low))
        for r, p in enumerate(low):
            for c, cell in enumerate([item(p.barcode), item(p.name),
                                      item(p.stock_quantity, right=True, color="#b07f00" if p.stock_quantity <= 0 else None),
                                      item(p.critical_stock_level, right=True)]):
                self.low_table.setItem(r, c, cell)
        ships = data["shipments"][:_LIST_ROWS]
        self.ship_table.setRowCount(len(ships))
        for r, s in enumerate(ships):
            status = live_status(s)
            for c, cell in enumerate([item(s.number), item(s.dealership_name),
                                      item(status, color=AMBER if status == "Delayed" else None), item(eta_text(s))]):
                self.ship_table.setItem(r, c, cell)
        recent = data["recent"]
        self.recent_table.setRowCount(len(recent))
        for r, m in enumerate(recent):
            sign = "+" if m["movement_type"] == "receive" else "−"
            for c, cell in enumerate([item(local_time_text(m["created_at"])), item(direction_label(m)), item(m["barcode"]),
                                      item(f"{sign}{m['quantity']}", right=True), item(reason_label(m))]):
                self.recent_table.setItem(r, c, cell)
