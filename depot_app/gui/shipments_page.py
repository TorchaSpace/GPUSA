"""Console > Shipments - where this warehouse plans, dispatches and tracks
shipments to dealerships, replacing the page's themed placeholder.

The Warehouse Console mockup lists "Shipments" in its sidebar but never
drew the page, so this one is built in the Console's own Industry style
from what the rest of the system needs: a "New shipment" form
(destination dealership, carrier, driver, ETA, product lines), and the
warehouse's shipment list with a detail panel and the actions that move
a shipment along - Dispatch (the truck left), Update ETA (it's running
late; the original promise is kept so the lateness shows everywhere),
Cancel. Receiving happens at the dealership (pos_app > Receive
Inventory); once it has, the detail panel shows what arrived line by
line, including any discrepancy the dealership reported.

Real: database.shipment_repository, statuses from shared.distribution.
Stock is per location: creating a shipment moves nothing; Dispatch takes
the lines off THIS warehouse's stock (and is refused if the warehouse is
short); Cancel after dispatch puts them back; the dealership's receipt
puts them on its shelf. The product picker shows what's on hand here.
Don't also log a shipment as an Outbound dispatch on the Floor - that's
for goods leaving the company.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateTimeEdit,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import dealership_repository, shipment_repository, stock_repository
from database.exceptions import DATABASE_ERRORS, InsufficientStockError
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.constants import SHIPMENT_POLL_INTERVAL_MS
from shared.distribution import duration_text, eta_text, lateness, live_status
from shared.formatting import local_datetime_text, parse_db_timestamp
from shared.gui_kit.polling import PollingTimer
from shared.models import UNASSIGNED, Shipment, StockLocation
from shared import current_session


def _kicker(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(f"font-size: 12px; letter-spacing: 1px; color: {INDUSTRY_PALETTE['text_secondary']};")
    return label


def _input_style() -> str:
    p = INDUSTRY_PALETTE
    return (
        f"background-color: {p['surface_raised']}; color: {p['text_primary']}; "
        f"border: 1px solid {p['border']}; border-radius: 0; padding: 6px 8px; font-size: 14px;"
    )


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
    return table


def _to_qdatetime(value: datetime) -> QDateTime:
    return QDateTime(value.year, value.month, value.day, value.hour, value.minute, 0)


def _from_qdatetime(value: QDateTime) -> datetime:
    d, t = value.date(), value.time()
    return datetime(d.year(), d.month(), d.day(), t.hour(), t.minute())  # naive = local time


class ShipmentsPage(QWidget):
    def __init__(self, origin: str, origin_code: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self._origin = origin  # the site label stamped on shipments
        self._origin_code = origin_code  # the warehouse whose stock they come out of
        self._shipments: list[Shipment] = []
        self._draft_lines: list[tuple[str, str, int]] = []  # (barcode, label, qty)
        self.setObjectName("shipmentsPage")
        self.setStyleSheet(f"#shipmentsPage {{ background-color: {p['background']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        title_row = QHBoxLayout()
        title_row.addWidget(_kicker(f"From {origin} to dealerships"), stretch=1)  # page name is in the Console header
        refresh = IndustryButton("Refresh", variant="ghost")
        refresh.clicked.connect(self.reload)
        title_row.addWidget(refresh, alignment=Qt.AlignTop)
        layout.addLayout(title_row)

        layout.addWidget(self._build_form())
        layout.addWidget(_kicker(f"Shipments · {origin}"))
        self._table = _table(["No.", "Destination", "Carrier", "Items", "Departed", "ETA", "Status", "Late"])
        header = self._table.horizontalHeader()
        for column in range(8):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.setMinimumHeight(220)
        self._table.itemSelectionChanged.connect(self._show_detail)
        layout.addWidget(self._table)
        layout.addWidget(self._build_detail())

        self._poller = PollingTimer(self._fetch, interval_ms=SHIPMENT_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_fetched)

        self.reload_choices()
        self.reload()

    # --- lifecycle: poll only while visible --------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload_choices()
        self._poller.start()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    # --- form ----------------------------------------------------------

    def _build_form(self) -> QWidget:
        p = INDUSTRY_PALETTE
        frame = BlueprintFrame(tick_color=p["text_primary"])
        frame.setObjectName("newShipment")
        frame.setStyleSheet(f"#newShipment {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        grid = QGridLayout(frame)
        grid.setContentsMargins(18, 16, 18, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)

        grid.addWidget(_kicker("New shipment"), 0, 0, 1, 4)
        self._dest_input = QComboBox()
        self._carrier_input = QLineEdit()
        self._carrier_input.setPlaceholderText("e.g. Ridgeline Freight")
        self._driver_input = QLineEdit()
        self._driver_input.setPlaceholderText("Optional")
        self._eta_input = QDateTimeEdit()
        self._eta_input.setCalendarPopup(True)
        self._eta_input.setDisplayFormat("dd.MM.yyyy HH:mm")
        for column, (caption, widget) in enumerate(
            (("Destination", self._dest_input), ("Carrier", self._carrier_input),
             ("Driver", self._driver_input), ("ETA", self._eta_input))
        ):
            widget.setStyleSheet(_input_style())
            grid.addWidget(_kicker(caption), 1, column)
            grid.addWidget(widget, 2, column)

        grid.addWidget(_kicker("Add product"), 3, 0, 1, 4)
        self._product_input = QComboBox()
        self._product_input.setStyleSheet(_input_style())
        self._qty_input = QSpinBox()
        self._qty_input.setRange(1, 1_000_000)
        self._qty_input.setStyleSheet(_input_style())
        add = IndustryButton("Add line", variant="ghost")
        add.clicked.connect(self._add_line)
        grid.addWidget(self._product_input, 4, 0, 1, 2)
        grid.addWidget(self._qty_input, 4, 2)
        grid.addWidget(add, 4, 3)

        self._lines_table = _table(["Product", "Qty"])
        self._lines_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._lines_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._lines_table.setMaximumHeight(150)
        grid.addWidget(self._lines_table, 5, 0, 1, 3)
        remove = IndustryButton("Remove line", variant="ghost")
        remove.clicked.connect(self._remove_line)
        grid.addWidget(remove, 5, 3, alignment=Qt.AlignTop)

        self._form_error = QLabel()
        self._form_error.setWordWrap(True)
        self._form_error.setStyleSheet(
            f"color: {p['text_primary']}; background-color: #fff6d6; border: 1px solid #f4b400; padding: 6px 10px; font-size: 12px;"
        )
        self._form_error.hide()
        grid.addWidget(self._form_error, 6, 0, 1, 4)

        bottom = QHBoxLayout()
        self._dispatch_now = QCheckBox("Truck is leaving now (dispatch immediately)")
        self._dispatch_now.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        bottom.addWidget(self._dispatch_now)
        bottom.addStretch(1)
        self._create_button = IndustryButton("Create shipment", variant="accent")
        self._create_button.clicked.connect(self._create)
        bottom.addWidget(self._create_button)
        grid.addLayout(bottom, 7, 0, 1, 4)
        return frame

    def reload_choices(self) -> None:
        """Dealerships, products and known carriers - re-read each time the
        page is shown (Admin may have added a dealership since)."""
        try:
            dealerships = [d for d in dealership_repository.list_all() if d.is_active]
            if self._origin_code:
                here = stock_repository.products_at(StockLocation.warehouse(self._origin_code))
            else:
                here = stock_repository.products_at(UNASSIGNED)
            products = [p for p in here if p.is_active]  # a deactivated product can't go on a shipment
            carriers = sorted({s.carrier for s in shipment_repository.list_shipments()}, key=str.casefold)
        except DATABASE_ERRORS:
            return
        current_dest, current_product = self._dest_input.currentData(), self._product_input.currentData()
        self._dest_input.clear()
        for dealership in dealerships:
            self._dest_input.addItem(f"{dealership.code} · {dealership.name} ({dealership.city})", dealership.code)
        self._product_input.clear()
        for product in products:
            self._product_input.addItem(f"{product.barcode} · {product.name} · {product.stock_quantity} here", product.barcode)
        for combo, value in ((self._dest_input, current_dest), (self._product_input, current_product)):
            index = combo.findData(value) if value is not None else -1
            if index >= 0:
                combo.setCurrentIndex(index)
        completer = QCompleter(carriers, self._carrier_input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._carrier_input.setCompleter(completer)
        if self._eta_input.dateTime() <= QDateTime.currentDateTime():
            self._eta_input.setDateTime(_to_qdatetime(datetime.now() + timedelta(hours=4)))
        self._create_button.setEnabled(bool(dealerships and products))
        if not dealerships:
            self._show_form_error("No active dealerships yet - add one in Admin > Dealerships (or open Admin from the setup folder).")

    def _add_line(self) -> None:
        barcode = self._product_input.currentData()
        if barcode is None:
            return
        quantity = self._qty_input.value()
        for index, (existing, label, qty) in enumerate(self._draft_lines):
            if existing == barcode:
                self._draft_lines[index] = (existing, label, qty + quantity)
                break
        else:
            self._draft_lines.append((barcode, self._product_input.currentText(), quantity))
        self._render_draft()

    def _remove_line(self) -> None:
        rows = self._lines_table.selectionModel().selectedRows()
        if rows:
            del self._draft_lines[rows[0].row()]
            self._render_draft()

    def _render_draft(self) -> None:
        self._lines_table.setRowCount(len(self._draft_lines))
        for row, (_barcode, label, qty) in enumerate(self._draft_lines):
            self._lines_table.setItem(row, 0, QTableWidgetItem(label))
            item = QTableWidgetItem(str(qty))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._lines_table.setItem(row, 1, item)

    def _show_form_error(self, message: str) -> None:
        self._form_error.setText(message)
        self._form_error.show()

    def _create(self) -> None:
        self._form_error.hide()
        code = self._dest_input.currentData()
        if code is None:
            self._show_form_error("Pick a destination dealership.")
            return
        try:
            shipment = shipment_repository.create(
                origin=self._origin,
                origin_code=self._origin_code,
                dealership_code=code,
                carrier=self._carrier_input.text(),
                driver=self._driver_input.text(),
                eta=_from_qdatetime(self._eta_input.dateTime()),
                lines=[(barcode, qty) for barcode, _label, qty in self._draft_lines],
            )
        except (ValueError, *DATABASE_ERRORS) as exc:  # blank carrier, bad ETA, inactive product, ...
            self._show_form_error(str(exc))
            return
        dispatch_problem = None
        if self._dispatch_now.isChecked():
            try:
                shipment = shipment_repository.dispatch(shipment.id, actor=current_session.actor())
            except InsufficientStockError as exc:
                dispatch_problem = (f" Not dispatched: only {exc.available} of {exc.barcode} on hand here "
                                    f"({exc.requested} needed) - it stays scheduled.")
            except (ValueError, *DATABASE_ERRORS) as exc:
                dispatch_problem = f" Not dispatched: {exc}"
        self._draft_lines = []
        self._render_draft()
        self._driver_input.clear()
        self._dispatch_now.setChecked(False)
        self.reload()
        self.select(shipment.id)
        self._set_message(f"{shipment.number} created for {shipment.dealership_name}"
                          + (" and dispatched." if shipment.status == "in_transit" else ".")
                          + (dispatch_problem or ""))
        self.reload_choices()  # on-hand counts changed

    # --- list + detail -------------------------------------------------

    def _build_detail(self) -> QWidget:
        p = INDUSTRY_PALETTE
        frame = BlueprintFrame(tick_color=p["text_primary"])
        frame.setObjectName("shipmentDetail")
        frame.setStyleSheet(f"#shipmentDetail {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(8)
        self._detail_title = QLabel("SELECT A SHIPMENT")
        self._detail_title.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(self._detail_title)
        self._detail_meta = QLabel()
        self._detail_meta.setWordWrap(True)
        self._detail_meta.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        layout.addWidget(self._detail_meta)
        self._detail_lines = _table(["Product", "Shipped", "Received", "Difference"])
        self._detail_lines.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._detail_lines.setSelectionMode(QAbstractItemView.NoSelection)
        self._detail_lines.setMaximumHeight(180)
        layout.addWidget(self._detail_lines)

        actions = QHBoxLayout()
        self._dispatch_button = IndustryButton("Dispatch", variant="primary")
        self._dispatch_button.clicked.connect(lambda: self._act("dispatch"))
        self._new_eta_input = QDateTimeEdit()
        self._new_eta_input.setCalendarPopup(True)
        self._new_eta_input.setDisplayFormat("dd.MM.yyyy HH:mm")
        self._new_eta_input.setStyleSheet(_input_style())
        self._eta_button = IndustryButton("Update ETA", variant="ghost")
        self._eta_button.clicked.connect(lambda: self._act("eta"))
        self._cancel_button = IndustryButton("Cancel shipment", variant="ghost")
        self._cancel_button.clicked.connect(lambda: self._act("cancel"))
        for widget in (self._dispatch_button, self._new_eta_input, self._eta_button, self._cancel_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self._message)
        self._show_detail()
        return frame

    def _fetch(self) -> list[Shipment] | None:
        try:
            shipments = shipment_repository.list_shipments()
        except Exception:  # a transient lock on a timer tick: try again next tick
            return None
        # This warehouse's shipments: by warehouse code, plus ones planned
        # before warehouses existed that carry only the site text.
        return [
            s for s in shipments
            if (self._origin_code and s.origin_code == self._origin_code)
            or (s.origin_code is None and s.origin == self._origin)
        ]

    def reload(self) -> None:
        self._on_fetched(self._fetch())

    def _on_fetched(self, shipments: list[Shipment] | None) -> None:
        if shipments is None:
            return
        selected = self.selected()
        # Active ones first (soonest ETA), then finished ones newest first.
        active = [s for s in shipments if s.is_active]
        done = sorted((s for s in shipments if not s.is_active), key=lambda s: s.delivered_at or s.eta, reverse=True)
        self._shipments = active + done
        self._render_table()
        if selected is not None:
            self.select(selected.id)
        else:
            self._show_detail()

    def _render_table(self) -> None:
        p = INDUSTRY_PALETTE
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._shipments))
        for row, shipment in enumerate(self._shipments):
            status = live_status(shipment)
            late = lateness(shipment)
            values = [
                shipment.number,
                f"{shipment.dealership_code} · {shipment.dealership_name}",
                shipment.carrier,
                str(shipment.item_count),
                local_datetime_text(shipment.departed_at),
                eta_text(shipment),
                status + (" · report" if shipment.discrepancies else ""),
                duration_text(late) if late.total_seconds() >= 60 else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 0:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == 6:
                    if status == "Delayed" or shipment.discrepancies:
                        item.setBackground(QColor(p["text_primary"]))
                        item.setForeground(QColor(p["background"]))
                    elif status in ("Delivered",):
                        item.setForeground(QColor(p["accent"]))
                    elif status == "Cancelled":
                        item.setForeground(QColor(p["text_secondary"]))
                self._table.setItem(row, column, item)
        self._table.blockSignals(False)

    def selected(self) -> Shipment | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._shipments):
            return None
        return self._shipments[rows[0].row()]

    def select(self, shipment_id: int) -> None:
        for row, shipment in enumerate(self._shipments):
            if shipment.id == shipment_id:
                self._table.selectRow(row)
                self._show_detail()
                return

    def shipments(self) -> list[Shipment]:
        return list(self._shipments)

    def _show_detail(self) -> None:
        shipment = self.selected()
        for button in (self._dispatch_button, self._eta_button, self._cancel_button):
            button.setEnabled(False)
        self._new_eta_input.setEnabled(False)
        if shipment is None:
            self._detail_title.setText("SELECT A SHIPMENT")
            self._detail_meta.setText("Pick a row above to see its lines and move it along.")
            self._detail_lines.setRowCount(0)
            return
        status = live_status(shipment)
        self._detail_title.setText(f"{shipment.number} · {status.upper()}")
        planned = parse_db_timestamp(shipment.planned_eta)
        meta = [
            f"To {shipment.dealership_code} · {shipment.dealership_name}",
            f"{shipment.carrier}" + (f" · Driver {shipment.driver}" if shipment.driver else ""),
            f"Promised {planned.astimezone().strftime('%d.%m.%Y %H:%M')}",
        ]
        late = lateness(shipment)
        if late.total_seconds() >= 60:
            meta.append(f"late {duration_text(late)}")
        if shipment.receipt_note:
            meta.append(f"Dealership note: “{shipment.receipt_note}”")
        self._detail_meta.setText(" · ".join(meta))

        self._detail_lines.setRowCount(len(shipment.lines))
        for row, line in enumerate(shipment.lines):
            received = "—" if line.received_qty is None else str(line.received_qty)
            diff = "" if line.received_qty is None else (f"{line.discrepancy:+d}" if line.discrepancy else "0")
            for column, value in enumerate((f"{line.product_barcode} · {line.product_name}", str(line.expected_qty), received, diff)):
                item = QTableWidgetItem(value)
                if column:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 3 and line.discrepancy:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._detail_lines.setItem(row, column, item)

        self._dispatch_button.setEnabled(shipment.status == "scheduled")
        for widget in (self._eta_button, self._cancel_button, self._new_eta_input):
            widget.setEnabled(shipment.is_active)
        if shipment.is_active:
            self._new_eta_input.setDateTime(_to_qdatetime(parse_db_timestamp(shipment.eta).astimezone().replace(tzinfo=None)))

    def _set_message(self, text: str) -> None:
        self._message.setText(text)

    def _act(self, action: str) -> None:
        shipment = self.selected()
        if shipment is None:
            return
        try:
            if action == "dispatch":
                updated = shipment_repository.dispatch(shipment.id, actor=current_session.actor())
                message = f"{updated.number} dispatched."
            elif action == "eta":
                updated = shipment_repository.update_eta(shipment.id, _from_qdatetime(self._new_eta_input.dateTime()))
                message = f"{updated.number} now due {eta_text(updated)}."
            else:
                updated = shipment_repository.cancel(shipment.id, actor=current_session.actor())
                message = f"{updated.number} cancelled."
        except InsufficientStockError as exc:
            message = (f"Couldn't dispatch {shipment.number}: only {exc.available} of {exc.barcode} "
                       f"on hand at {exc.location or 'this warehouse'} ({exc.requested} needed).")
            updated = shipment
        except (ValueError, *DATABASE_ERRORS) as exc:  # e.g. an ETA before the departure, a vanished location
            message = f"Couldn't update: {exc}"
            updated = shipment
        self.reload()
        if action in ("dispatch", "cancel"):
            self.reload_choices()
        self.select(updated.id)
        self._set_message(message)
