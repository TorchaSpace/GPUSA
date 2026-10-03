"""Admin > Warehouses - the real build of the Warehouses mockup, replacing
its themed placeholder.

Recreated from the mockup:
- KPI row: warehouses, units held in them, how many are near capacity
  (the mockup's "85% threshold"), plus unassigned stock (see below).
- The network row: one card per warehouse (name, city, docks, capacity %
  with its bar, status "Near capacity" at/above 85% else "Operational",
  on shift / rostered, inbound / outbound today). Clicking a card filters
  the tabs below to that site; clicking it again clears the filter.
- "Movement Logs": Time, Movement (Check-in / Check-out), SKU, Product,
  Qty, Site, plus Why and Reference, with an All / Inbound / Outbound
  filter.
- "Workforce Attendance": today's roster of warehouse employees.

Real: warehouse_repository, stock_repository (per-location stock),
attendance_repository. Deliberately different from the mockup, and why:
- Capacity is in UNITS of stock, not pallets (nothing records pallets).
  A warehouse without a capacity set shows "Capacity not set".
- Docks is how many a warehouse has; the mockup's "6/8 in use" isn't
  tracked, so it isn't shown.
- "Handled by" is whoever was signed in (Console / Admin / a till); the
  depot Floor is an open kiosk, so its receipts and dispatches show "—".
- An employee belongs to a warehouse when their Location (Workforce) is
  the warehouse's code, name or "code · name" - employees name their
  place as free text.
- Added, because per-location stock needs them: a "Stock by location"
  tab (every product across warehouses / dealerships / unassigned / on
  the road), "Move / count stock" (StockMovePopup), add/edit/delete a
  warehouse, and the Unassigned banner with "Place all here" for stock
  that existed before warehouses were tracked.
Each depot instance registers its own warehouse from the setup wizard
(see shared/warehouse_bootstrap.py), so these normally appear on their
own; "Add warehouse" is for anything else.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard
from admin_app.gui.components.stock_move_popup import StockMovePopup
from admin_app.gui.components.styled_table import cell, styled_table
from admin_app.gui.components.warehouse_capacity_card import WarehouseCapacityCard
from admin_app.gui.components.warehouse_form_popup import WarehouseFormPopup
from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE
from database import (
    attendance_repository,
    dealership_repository,
    product_repository,
    stock_repository,
    warehouse_repository,
)
from database.exceptions import DataAccessError
from shared.constants import WAREHOUSE_POLL_INTERVAL_MS
from shared.formatting import local_time_text
from shared.gui_kit.polling import PollingTimer
from shared.models import UNASSIGNED, StockLocation
from shared import current_session
from shared.warehousing import (
    STATUS_NEAR,
    capacity_status,
    direction_label,
    reason_label,
    reference_text,
    start_of_today_db,
    works_at,
)

_CARDS_PER_ROW = 3
_TABS = (("moves", "Movement Logs"), ("workforce", "Workforce Attendance"), ("stock", "Stock by location"))
_DIRECTIONS = ("All", "Inbound", "Outbound")


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


def _segments(labels, on_click) -> tuple[QWidget, dict[str, _Segment]]:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    group = QButtonGroup(box)
    group.setExclusive(True)
    buttons = {}
    for key, label in labels:
        button = _Segment(label.replace("&", "&&"))
        button.clicked.connect(lambda _checked=False, k=key: on_click(k))
        group.addButton(button)
        row.addWidget(button)
        buttons[key] = button
    return box, buttons


class WarehousesPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.warehouses.title"), parent, subtitle=tr("page.warehouses.subtitle"))
        self._data: dict | None = None
        self._site: str | None = None  # card filter (warehouse code)
        self._tab = "moves"
        self._direction = "All"
        self._cards: list[WarehouseCapacityCard] = []

        add = CompactButton("Add warehouse", variant="primary")
        add.clicked.connect(self._open_add)
        move = CompactButton("Move / count stock")
        move.clicked.connect(self._open_move)
        refresh = CompactButton(tr("admin.refresh"))
        refresh.clicked.connect(self.reload)
        for button in (add, move, refresh):
            self.add_header_action(button)

        self.body_layout().addWidget(self._build_kpis())
        self.body_layout().addWidget(self._build_unassigned_banner())
        self.body_layout().addWidget(self._build_network())
        self.body_layout().addWidget(self._build_activity())

        self._form = WarehouseFormPopup(self)
        self._form.accepted.connect(self._save_form)
        self._move_popup = StockMovePopup(self)
        self._move_popup.stock_changed.connect(self.reload)

        self._poller = PollingTimer(self._fetch, interval_ms=WAREHOUSE_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_fetched)
        self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()
        self._poller.start()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    # --- building ------------------------------------------------------------

    def _build_kpis(self) -> QWidget:
        p = CLASSICAL_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self._count_card = StatCard("Warehouses", "—")
        self._units_card = StatCard("Units in warehouses", "—")
        self._near_card = StatCard("Near capacity", "—", corner_note="85% threshold")
        self._unassigned_card = StatCard("Unassigned stock", "—")
        self._kpi_notes = {}
        for key, card in (("count", self._count_card), ("units", self._units_card),
                          ("near", self._near_card), ("unassigned", self._unassigned_card)):
            note = QLabel()
            note.setWordWrap(True)
            note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
            card.footer_layout().addWidget(note)
            self._kpi_notes[key] = note
            layout.addWidget(card, stretch=1)
        return row

    def _build_unassigned_banner(self) -> QWidget:
        p = CLASSICAL_PALETTE
        self._banner = QFrame()
        self._banner.setObjectName("unassignedBanner")
        self._banner.setStyleSheet(
            f"#unassignedBanner {{ background-color: rgba(223, 148, 96, 22); border: 1px solid {p['alert_warning']}; "
            f"border-radius: {p['radius_md']}; }}"
        )
        layout = QHBoxLayout(self._banner)
        layout.setContentsMargins(16, 10, 16, 10)
        self._banner_text = QLabel()
        self._banner_text.setWordWrap(True)
        self._banner_text.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        layout.addWidget(self._banner_text, stretch=1)
        self._place_target = QComboBox()
        self._place_target.setMinimumWidth(220)
        layout.addWidget(self._place_target)
        place = CompactButton("Place all here")
        place.clicked.connect(self._place_all_unassigned)
        layout.addWidget(place)
        self._banner.hide()
        return self._banner

    def _build_network(self) -> Section:
        p = CLASSICAL_PALETTE
        self._network = Section("Network", "Warehouse capacity")
        self._edit_button = CompactButton("Edit")
        self._edit_button.clicked.connect(self._open_edit)
        self._delete_button = CompactButton("Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        for button in (self._edit_button, self._delete_button):
            self._network.add_header_control(button)
        body = QWidget()
        self._grid = QGridLayout(body)
        self._grid.setContentsMargins(16, 16, 16, 16)
        self._grid.setSpacing(12)
        self._network.body_layout().addWidget(body)
        self._empty_network = QLabel(
            "No warehouses yet. Each Depot instance registers its own from the setup wizard - "
            "or add one with “Add warehouse”."
        )
        self._empty_network.setWordWrap(True)
        self._empty_network.setAlignment(Qt.AlignCenter)
        self._empty_network.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']}; padding: 20px;")
        self._network.body_layout().addWidget(self._empty_network)
        return self._network

    def _build_activity(self) -> Section:
        p = CLASSICAL_PALETTE
        self._activity = Section("Activity", "All warehouses")
        tabs_box, self._tab_buttons = _segments(_TABS, self.set_tab)
        self._activity.add_header_control(tabs_box)

        self._stack = QStackedWidget()
        # Movement logs
        moves = QWidget()
        moves_layout = QVBoxLayout(moves)
        moves_layout.setContentsMargins(16, 8, 16, 16)
        filter_row = QHBoxLayout()
        dir_box, self._dir_buttons = _segments([(d, d) for d in _DIRECTIONS], self.set_direction)
        filter_row.addWidget(dir_box)
        filter_row.addStretch(1)
        self._moves_count = QLabel()
        self._moves_count.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        filter_row.addWidget(self._moves_count)
        moves_layout.addLayout(filter_row)
        self._moves_table = styled_table(["Time", "Movement", "SKU", "Product", "Qty", "Site", "Why", "Reference", "Handled by"])
        self._moves_table.setMinimumHeight(320)
        header = self._moves_table.horizontalHeader()
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        moves_layout.addWidget(self._moves_table)
        self._stack.addWidget(moves)
        # Workforce
        people = QWidget()
        people_layout = QVBoxLayout(people)
        people_layout.setContentsMargins(16, 8, 16, 16)
        self._people_note = QLabel()
        self._people_note.setWordWrap(True)
        self._people_note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        people_layout.addWidget(self._people_note)
        self._people_table = styled_table(["Badge", "Name", "Role", "Site", "Status", "In", "Out", "Hours"])
        self._people_table.setMinimumHeight(320)
        self._people_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        people_layout.addWidget(self._people_table)
        self._stack.addWidget(people)
        # Stock by location
        stock = QWidget()
        stock_layout = QVBoxLayout(stock)
        stock_layout.setContentsMargins(16, 8, 16, 16)
        self._stock_note = QLabel()
        self._stock_note.setWordWrap(True)
        self._stock_note.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        stock_layout.addWidget(self._stock_note)
        self._stock_table = styled_table(["Product", "SKU"])
        self._stock_table.setMinimumHeight(320)
        stock_layout.addWidget(self._stock_table)
        self._stack.addWidget(stock)

        self._activity.body_layout().addWidget(self._stack)
        self._tab_buttons["moves"].setChecked(True)
        self._dir_buttons["All"].setChecked(True)
        return self._activity

    # --- data ----------------------------------------------------------------

    def _fetch(self) -> dict | None:
        try:
            warehouses = warehouse_repository.list_all()
            return {
                "warehouses": warehouses,
                "used": stock_repository.units_by_location(),
                "today": stock_repository.movement_totals_since(start_of_today_db()),
                "roster": attendance_repository.list_roster(),
                "movements": stock_repository.list_movements(limit=300, location_kind="warehouse"),
                "levels": stock_repository.all_levels(),
                "products": product_repository.list_all(),
                "dealerships": dealership_repository.list_all(),
            }
        except Exception:  # a transient lock on a timer tick: keep what's shown
            return None

    def reload(self) -> None:
        self._on_fetched(self._fetch())

    def _on_fetched(self, data: dict | None) -> None:
        if data is None:
            return
        self._data = data
        codes = {w.code for w in data["warehouses"]}
        if self._site not in codes:
            self._site = None
        self._render_kpis()
        self._render_banner()
        self._render_cards()
        self._render_tab()

    def warehouses(self):
        return list(self._data["warehouses"]) if self._data else []

    def _roster_for(self, warehouse) -> list[dict]:
        return [r for r in self._data["roster"] if works_at(r["location_type"], r["location_name"], warehouse)]

    # --- rendering -------------------------------------------------------------

    def _render_kpis(self) -> None:
        d = self._data
        active = [w for w in d["warehouses"] if w.is_active]
        used = d["used"]
        held = sum(used.get(w.location, 0) for w in d["warehouses"])
        capacity = sum(w.capacity_units or 0 for w in active)
        near = [w.code for w in active if capacity_status(w, used.get(w.location, 0)) == STATUS_NEAR]
        unassigned = used.get(UNASSIGNED, 0)
        self._count_card.set_value(str(len(active)))
        inactive = len(d["warehouses"]) - len(active)
        self._kpi_notes["count"].setText(f"active · {inactive} inactive" if inactive else "active")
        self._units_card.set_value(f"{held:,}")
        self._kpi_notes["units"].setText(
            f"of {capacity:,} units of set capacity" if capacity else "no capacities set yet"
        )
        self._near_card.set_value(str(len(near)))
        self._kpi_notes["near"].setText(", ".join(near) if near else "all below 85%")
        self._unassigned_card.set_value(f"{unassigned:,}")
        self._kpi_notes["unassigned"].setText("units not placed at any location" if unassigned else "everything is placed")

    def _render_banner(self) -> None:
        unassigned = self._data["used"].get(UNASSIGNED, 0)
        active = [w for w in self._data["warehouses"] if w.is_active]
        self._banner.setVisible(bool(unassigned and active))
        self._banner_text.setText(
            f"<b>{unassigned:,} units</b> aren't at any warehouse or dealership yet - stock from before "
            f"warehouses were tracked, or added in Inventory. Put them where they physically are, or use "
            f"“Move / count stock” for part of them."
        )
        current = self._place_target.currentData()
        self._place_target.clear()
        for w in active:
            self._place_target.addItem(w.site_label, w.code)
        index = self._place_target.findData(current)
        if index >= 0:
            self._place_target.setCurrentIndex(index)

    def _render_cards(self) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._cards = []
        d = self._data
        for index, w in enumerate(d["warehouses"]):
            card = WarehouseCapacityCard(w)
            roster = self._roster_for(w)
            rostered = sum(1 for r in roster if r["is_active"])
            on_shift = sum(1 for r in roster if r["status"] == "Present")
            inbound, outbound = d["today"].get(w.location, (0, 0))
            card.set_numbers(d["used"].get(w.location, 0), on_shift, rostered, inbound, outbound)
            card.set_selected(w.code == self._site)
            card.clicked.connect(self.select_site)
            self._grid.addWidget(card, index // _CARDS_PER_ROW, index % _CARDS_PER_ROW)
            self._cards.append(card)
        self._empty_network.setVisible(not d["warehouses"])
        self._edit_button.setEnabled(self._site is not None)
        self._delete_button.setEnabled(self._site is not None)
        self._network.set_kicker(f"Network · {len(d['warehouses'])} warehouse{'s' if len(d['warehouses']) != 1 else ''}")

    def _selected_warehouse(self):
        return next((w for w in self.warehouses() if w.code == self._site), None)

    def _render_tab(self) -> None:
        selected = self._selected_warehouse()
        self._activity.set_kicker(f"Activity · {selected.site_label}" if selected else "Activity · all warehouses")
        self._stack.setCurrentIndex([key for key, _ in _TABS].index(self._tab))
        {"moves": self._render_moves, "workforce": self._render_people, "stock": self._render_stock}[self._tab]()

    def _render_moves(self) -> None:
        p = CLASSICAL_PALETTE
        rows = self._data["movements"]
        if self._site:
            rows = [m for m in rows if m["location"] == StockLocation.warehouse(self._site)]
        if self._direction != "All":
            wanted = "receive" if self._direction == "Inbound" else "dispatch"
            rows = [m for m in rows if m["movement_type"] == wanted]
        self._moves_count.setText(f"{len(rows)} movement{'s' if len(rows) != 1 else ''} · newest first")
        table = self._moves_table
        table.setRowCount(len(rows))
        for r, m in enumerate(rows):
            inbound = m["movement_type"] == "receive"
            values = [
                cell(local_time_text(m["created_at"])),
                cell(direction_label(m), color=p["alert_success"] if inbound else p["accent"]),
                cell(m["barcode"]),
                cell(m["product_name"]),
                cell(f"{'+' if inbound else '−'}{m['quantity']:,}", right=True),
                cell(m["location"].code if m["location"] else "—"),
                cell(reason_label(m)),
                cell(reference_text(m)),
                cell(m.get("handled_by") or "—"),
            ]
            for c, item in enumerate(values):
                table.setItem(r, c, item)

    def _render_people(self) -> None:
        selected = self._selected_warehouse()
        warehouses = [selected] if selected else self.warehouses()
        rows = []
        for w in warehouses:
            rows += [(w, r) for r in self._roster_for(w) if r["is_active"]]
        unmatched = [r for r in self._data["roster"] if r["location_type"] == "Warehouse" and r["is_active"]
                     and not any(works_at(r["location_type"], r["location_name"], w) for w in self.warehouses())]
        note = "Today's roster · an employee belongs to a warehouse when their Location (Workforce) is its code or name."
        if unmatched and not selected:
            names = ", ".join(sorted({r["location_name"] for r in unmatched}))
            note += f" {len(unmatched)} warehouse employee(s) name a location that isn't a warehouse here: {names}."
        self._people_note.setText(note)
        table = self._people_table
        table.setRowCount(len(rows))
        for r, (w, entry) in enumerate(rows):
            values = [
                entry["badge_id"], entry["name"], entry["role"], w.code, entry["status"],
                local_time_text(entry["check_in_at"]) if entry["check_in_at"] else "—",
                local_time_text(entry["check_out_at"]) if entry["check_out_at"] else "—",
                f"{entry['hours']:.1f}" if entry["hours"] is not None else "—",
            ]
            for c, value in enumerate(values):
                table.setItem(r, c, cell(value))

    def _render_stock(self) -> None:
        d = self._data
        warehouses = [self._selected_warehouse()] if self._site else d["warehouses"]
        headers = ["Product", "SKU"] + [w.code for w in warehouses]
        if not self._site:
            headers += ["Dealerships", "Unassigned", "On the road", "Company total"]
        per: dict[str, dict] = {}
        for level in d["levels"]:
            per.setdefault(level.product_barcode, {})[level.location] = level.quantity
        products = d["products"]
        if self._site:
            here = StockLocation.warehouse(self._site)
            products = [pr for pr in products if per.get(pr.barcode, {}).get(here)]
        table = self._stock_table
        table.clear()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.setRowCount(len(products))
        for r, product in enumerate(products):
            levels = per.get(product.barcode, {})
            values = [cell(product.name), cell(product.barcode)]
            values += [cell(f"{levels.get(w.location, 0):,}", right=True) for w in warehouses]
            if not self._site:
                at_dealers = sum(q for loc, q in levels.items() if loc.kind == "dealership")
                unassigned = levels.get(UNASSIGNED, 0)
                road = product.stock_quantity - sum(levels.values())
                values += [cell(f"{at_dealers:,}", right=True), cell(f"{unassigned:,}", right=True),
                           cell(f"{road:,}" if road else "—", right=True),
                           cell(f"{product.stock_quantity:,}", right=True)]
            for c, item in enumerate(values):
                table.setItem(r, c, item)
        self._stock_note.setText(
            f"Units of each product held at {warehouses[0].site_label}." if self._site else
            "Every product across the company: per warehouse, on dealership shelves, not placed yet, and on "
            "trucks (dispatched shipments not yet received). Company total = all of these."
        )

    # --- interaction -----------------------------------------------------------

    def select_site(self, code: str | None) -> None:
        """Toggle the card filter (clicking the selected card clears it)."""
        self._site = None if code == self._site else code
        for card in self._cards:
            card.set_selected(card.warehouse.code == self._site)
        self._edit_button.setEnabled(self._site is not None)
        self._delete_button.setEnabled(self._site is not None)
        if self._data:
            self._render_tab()

    def set_tab(self, key: str) -> None:
        self._tab = key
        self._tab_buttons[key].setChecked(True)
        if self._data:
            self._render_tab()

    def set_direction(self, name: str) -> None:
        self._direction = name
        self._dir_buttons[name].setChecked(True)
        if self._data:
            self._render_tab()

    def _open_add(self) -> None:
        self._form.open_or_refresh(warehouse=None)

    def _open_edit(self) -> None:
        warehouse = self._selected_warehouse()
        if warehouse is not None:
            self._form.open_or_refresh(warehouse=warehouse)

    def _save_form(self) -> None:
        try:
            warehouse = self._form.result_warehouse()
            if self._form.is_editing():
                warehouse_repository.update(warehouse)
            else:
                warehouse_repository.create(warehouse)
        except (DataAccessError, ValueError) as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()

    def _delete_selected(self) -> None:
        warehouse = self._selected_warehouse()
        if warehouse is None:
            return
        confirm = QMessageBox.question(self, "Delete warehouse?", f"Delete {warehouse.site_label}? This cannot be undone.")
        if confirm != QMessageBox.Yes:
            return
        try:
            warehouse_repository.delete(warehouse.code)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't delete", str(exc))
            return
        self._site = None
        self.reload()

    def _open_move(self) -> None:
        if not self._data:
            return
        active = [w for w in self._data["warehouses"] if w.is_active]
        selected = self._selected_warehouse()
        self._move_popup.set_choices(
            self._data["products"], active, self._data["dealerships"],
            source=UNASSIGNED if self._data["used"].get(UNASSIGNED) else (selected.location if selected else None),
            destination=selected.location if selected else None,
        )
        self._move_popup.show()
        self._move_popup.raise_()

    def place_all_unassigned(self, code: str) -> int:
        """Move every unassigned unit to warehouse `code`; returns units moved."""
        moved = stock_repository.place_all_unassigned(StockLocation.warehouse(code), actor=current_session.actor())
        self.reload()
        return moved

    def _place_all_unassigned(self) -> None:
        code = self._place_target.currentData()
        if code is None:
            return
        units = self._data["used"].get(UNASSIGNED, 0) if self._data else 0
        confirm = QMessageBox.question(
            self, "Place unassigned stock?",
            f"Record all {units:,} unassigned units as being at {self._place_target.currentText()}?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.place_all_unassigned(code)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't place stock", str(exc))
