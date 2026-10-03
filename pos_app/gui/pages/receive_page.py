"""Receive Inventory - the real build of the POS mockup's "Receive
Inventory" screen, replacing its themed placeholder.

Recreated from the mockup:
- Left: "Incoming" - one card per shipment coming to this dealership
  (number, status tag, where it's from, ETA · N lines).
- Right: the selected shipment - "<origin> · Driver <name>", "Shipment
  SH-…", the Accept All / Report Discrepancy pill, one row per product
  (check circle, product, Expected, Received with −/+ in report mode),
  and the footer "N of M checked · K discrepancies" with Complete
  receipt / Send report & receive.

Real: database.shipment_repository (created and dispatched by the depot's
Console > Shipments). Completing a receipt marks the shipment delivered,
records what arrived line by line, and puts the received units on THIS
dealership's shelf (where the count differs from what was shipped, the
difference is written off / added - see shipment_repository's
docstring); the depot and admin_app see the report straight away.

Which shipments: the ones addressed to THIS terminal's dealership (its
setup file's code - shared.dealership_bootstrap.load_dealership_identity).
A terminal with no setup file (e.g. a `python -m pos_app.main` dev run)
lists every incoming shipment and says so, rather than showing nothing.

Deliberately different from the mockup: the tags are the real, derived
status ("Arriving" = due within the hour, "Delayed", "On the way",
"Scheduled", "Received") instead of the mockup's "At door" - nothing
tells this system a truck is physically at the door. And reporting a
discrepancy offers an optional note (what happened), which the depot
sees on the shipment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from database import shipment_repository
from database.exceptions import DataAccessError
from pos_app.theme import FONT_HEADING, ORGANIC_PALETTE
from shared.constants import SHIPMENT_POLL_INTERVAL_MS
from shared.distribution import eta_text, live_status
from shared.formatting import parse_db_timestamp
from shared.gui_kit.polling import PollingTimer
from shared.models import Shipment
from shared import current_session

_YELLOW = "#f2c230"
_RED_TEXT = "#9a2a1d"
_RECENT = timedelta(hours=24)  # keep a received shipment on the list this long

_TAGS = {  # live status -> (tag text, background, text colour)
    "Arriving": ("Arriving", "#f3d9c6", "#7a3d15"),
    "Delayed": ("Delayed", _YELLOW, "#3a2a05"),
    "In Transit": ("On the way", "#e3dccb", "#4a4336"),
    "Scheduled": ("Scheduled", "#e3dccb", "#4a4336"),
    "Delivered": ("Received", "#dfe6d3", "#3d472b"),
}


def _round_button(size: int, bg: str, fg: str, border: str = "none") -> str:
    return (
        f"QPushButton {{ background-color: {bg}; color: {fg}; border: {border}; border-radius: {size // 2}px; "
        f"font-size: 20px; font-weight: 700; min-width: {size}px; max-width: {size}px; "
        f"min-height: {size}px; max-height: {size}px; }}"
    )


class _ShipmentCard(QPushButton):
    def __init__(self, shipment: Shipment, selected: bool, parent=None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(110)
        self.setObjectName("shipCard")
        self.setStyleSheet(
            f"#shipCard {{ background-color: {p['surface_raised'] if selected else p['surface']}; border: none; "
            f"border-radius: 28px; text-align: left; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)
        top = QHBoxLayout()
        number = QLabel(shipment.number)
        number.setStyleSheet(f"font-weight: 700; font-size: 17px; color: {p['text_primary']};")
        top.addWidget(number)
        top.addStretch(1)
        text, bg, fg = _TAGS.get(live_status(shipment), _TAGS["Scheduled"])
        self.tag = QLabel(text)
        self.tag.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 12px; padding: 4px 10px; font-size: 13px; font-weight: 700;"
        )
        top.addWidget(self.tag)
        layout.addLayout(top)
        origin = QLabel(shipment.origin)
        eta = QLabel(f"{eta_text(shipment)} · {len(shipment.lines)} lines")
        for label in (origin, eta):
            label.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
            layout.addWidget(label)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)


class ReceivePage(QWidget):
    """Emits `stock_changed` after a receipt that moved stock (a
    discrepancy), so the host can refresh stock views and badges."""

    stock_changed = Signal()
    incoming_changed = Signal()

    def __init__(self, dealership_code: str | None, dealership_name: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self._code = dealership_code
        self._dealer_name = dealership_name
        self._shipments: list[Shipment] = []
        self._selected_id: int | None = None
        self._checked: dict[int, set[str]] = {}
        self._received: dict[int, dict[str, int]] = {}
        self._report_mode: dict[int, bool] = {}
        self.setObjectName("receivePage")
        # Containers named "clear" let the panel behind them show through
        # (the app-wide base stylesheet otherwise paints every widget).
        self.setStyleSheet(
            f"#receivePage {{ background-color: {p['background']}; }} "
            f"QWidget#clear {{ background: transparent; }}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 28)
        outer.setSpacing(24)

        left = QVBoxLayout()
        left.setSpacing(12)
        heading = QLabel("Incoming")
        heading.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 34px; color: {p['text_primary']};")
        left.addWidget(heading)
        self._scope_note = QLabel()
        self._scope_note.setWordWrap(True)
        self._scope_note.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        left.addWidget(self._scope_note)
        cards_host = QWidget()
        cards_host.setObjectName("clear")
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(12)
        self._cards_layout.addStretch(1)
        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setFrameShape(QScrollArea.NoFrame)
        cards_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        cards_scroll.viewport().setObjectName("clear")
        cards_scroll.setWidget(cards_host)
        left.addWidget(cards_scroll, stretch=1)
        left_widget = QWidget()
        left_widget.setObjectName("clear")
        left_widget.setLayout(left)
        left_widget.setFixedWidth(320)
        outer.addWidget(left_widget)

        outer.addWidget(self._build_detail(), stretch=1)

        self._poller = PollingTimer(self._fetch, interval_ms=SHIPMENT_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_fetched)
        self.reload()

    # --- lifecycle -----------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._poller.start()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    # --- detail panel ----------------------------------------------------

    def _build_detail(self) -> QWidget:
        p = ORGANIC_PALETTE
        panel = QWidget()
        panel.setObjectName("receiveDetail")
        panel.setAttribute(Qt.WA_StyledBackground, True)
        panel.setStyleSheet(f"#receiveDetail {{ background-color: {p['surface_raised']}; border-radius: 36px; }}")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(28, 24, 28, 18)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._from_label = QLabel()
        self._from_label.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        self._title_label = QLabel()
        self._title_label.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 32px; color: {p['text_primary']};")
        titles.addWidget(self._from_label)
        titles.addWidget(self._title_label)
        head.addLayout(titles, stretch=1)

        pill = QWidget()
        pill.setObjectName("receivePill")
        pill.setAttribute(Qt.WA_StyledBackground, True)
        pill.setStyleSheet(f"#receivePill {{ background-color: {p['surface']}; border-radius: 34px; }}")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(6, 6, 6, 6)
        pill_layout.setSpacing(6)
        self._accept_button = QPushButton("✓  Accept All")
        self._accept_button.clicked.connect(self._accept_all)
        self._report_button = QPushButton("⚠  Report Discrepancy")
        self._report_button.clicked.connect(self._toggle_report)
        for button in (self._accept_button, self._report_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(56)
            pill_layout.addWidget(button)
        head.addWidget(pill, alignment=Qt.AlignBottom)
        layout.addLayout(head)

        columns = QHBoxLayout()
        columns.setContentsMargins(28, 0, 40, 10)
        for text, width, align in (("", 64, Qt.AlignLeft), ("Product", 0, Qt.AlignLeft),
                                   ("Expected", 110, Qt.AlignRight), ("Received", 190, Qt.AlignRight)):
            label = QLabel(text.upper())
            label.setStyleSheet(f"font-size: 13px; font-weight: 700; letter-spacing: 1px; color: {p['text_secondary']};")
            label.setAlignment(align)
            if width:
                label.setFixedWidth(width)
                columns.addWidget(label)
            else:
                columns.addWidget(label, stretch=1)
        layout.addLayout(columns)

        rows_host = QWidget()
        rows_host.setObjectName("clear")
        self._rows_layout = QVBoxLayout(rows_host)
        self._rows_layout.setContentsMargins(16, 0, 16, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)
        rows_scroll = QScrollArea()
        rows_scroll.setWidgetResizable(True)
        rows_scroll.setFrameShape(QScrollArea.NoFrame)
        rows_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        rows_scroll.viewport().setObjectName("clear")
        rows_scroll.setWidget(rows_host)
        layout.addWidget(rows_scroll, stretch=1)

        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText("What happened? (optional - the warehouse sees this)")
        self._note_input.setStyleSheet(
            f"background-color: {p['background']}; color: {p['text_primary']}; border: none; border-radius: 20px; "
            f"padding: 10px 16px; font-size: 15px; margin: 8px 28px;"
        )
        layout.addWidget(self._note_input)

        footer = QWidget()
        footer.setObjectName("receiveFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        footer.setStyleSheet(
            f"#receiveFooter {{ background-color: {p['surface']}; border-bottom-left-radius: 36px; border-bottom-right-radius: 36px; }}"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(28, 18, 28, 18)
        self._count_label = QLabel()
        self._count_label.setTextFormat(Qt.RichText)
        self._count_label.setStyleSheet(f"font-size: 17px; color: {p['text_primary']};")
        footer_layout.addWidget(self._count_label, stretch=1)
        self._complete_button = QPushButton()
        self._complete_button.setCursor(Qt.PointingHandCursor)
        self._complete_button.setFixedHeight(60)
        self._complete_button.clicked.connect(self._complete)
        footer_layout.addWidget(self._complete_button)
        layout.addWidget(footer)

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']}; padding: 8px 28px;")
        layout.addWidget(self._message)
        return panel

    # --- data ---------------------------------------------------------

    def _fetch(self) -> list[Shipment] | None:
        try:
            shipments = shipment_repository.list_shipments(
                statuses=("scheduled", "in_transit", "delivered"), dealership_code=self._code
            )
        except Exception:  # transient lock on a timer tick
            return None
        cutoff = datetime.now(timezone.utc) - _RECENT
        return [
            s for s in shipments
            if s.is_active or (s.delivered_at and parse_db_timestamp(s.delivered_at) >= cutoff)
        ]

    def reload(self) -> None:
        self._on_fetched(self._fetch())

    def _on_fetched(self, shipments: list[Shipment] | None) -> None:
        if shipments is None:
            return
        # Still-coming ones first (soonest ETA), received ones after.
        self._shipments = [s for s in shipments if s.is_active] + [s for s in shipments if not s.is_active]
        ids = {s.id for s in self._shipments}
        if self._selected_id not in ids:
            self._selected_id = self._shipments[0].id if self._shipments else None
        if self._code is None:
            self._scope_note.setText(
                "This terminal isn't linked to a dealership (no setup file), so every incoming shipment is listed."
            )
        else:
            self._scope_note.setText(f"Shipments to {self._dealer_name or self._code}")
        self._render()
        self.incoming_changed.emit()

    def shipments(self) -> list[Shipment]:
        return list(self._shipments)

    def selected(self) -> Shipment | None:
        return next((s for s in self._shipments if s.id == self._selected_id), None)

    def select(self, shipment_id: int) -> None:
        self._selected_id = shipment_id
        self._render()

    # --- per-shipment working state ------------------------------------

    def _received_for(self, shipment: Shipment, barcode: str) -> int:
        line = next(l for l in shipment.lines if l.product_barcode == barcode)
        if line.received_qty is not None:
            return line.received_qty
        return self._received.get(shipment.id, {}).get(barcode, line.expected_qty)

    def _is_checked(self, shipment: Shipment, barcode: str) -> bool:
        return shipment.status == "delivered" or barcode in self._checked.get(shipment.id, set())

    def toggle_line(self, barcode: str) -> None:
        shipment = self.selected()
        if shipment is None or shipment.status == "delivered":
            return
        checked = self._checked.setdefault(shipment.id, set())
        checked.symmetric_difference_update({barcode})
        self._render()

    def change_received(self, barcode: str, delta: int) -> None:
        shipment = self.selected()
        if shipment is None or shipment.status == "delivered":
            return
        received = self._received.setdefault(shipment.id, {})
        received[barcode] = max(0, self._received_for(shipment, barcode) + delta)
        self._checked.setdefault(shipment.id, set()).add(barcode)
        self._render()

    def _accept_all(self) -> None:
        shipment = self.selected()
        if shipment is None or shipment.status == "delivered":
            return
        self._checked[shipment.id] = {l.product_barcode for l in shipment.lines}
        self._received[shipment.id] = {l.product_barcode: l.expected_qty for l in shipment.lines}
        self._report_mode[shipment.id] = False
        self._render()

    def _toggle_report(self) -> None:
        shipment = self.selected()
        if shipment is None or shipment.status == "delivered":
            return
        self._report_mode[shipment.id] = not self._report_mode.get(shipment.id, False)
        self._render()

    def _complete(self) -> None:
        shipment = self.selected()
        if shipment is None or shipment.status == "delivered":
            return
        received = {l.product_barcode: self._received_for(shipment, l.product_barcode) for l in shipment.lines}
        note = self._note_input.text() if self._report_mode.get(shipment.id) else None
        try:
            done = shipment_repository.complete_receipt(shipment.id, received, note, actor=current_session.actor())
        except (DataAccessError, ValueError) as exc:
            self._message.setText(f"Couldn't complete: {exc}")
            self.reload()
            return
        self._note_input.clear()
        issues = len(done.discrepancies)
        self._message.setText(
            f"{done.number} received · report sent to the warehouse" if issues else f"{done.number} received into stock"
        )
        self.reload()
        self.stock_changed.emit()  # every receipt fills this shelf

    # --- rendering -------------------------------------------------------

    def _render(self) -> None:
        self._render_cards()
        self._render_detail()

    def _render_cards(self) -> None:
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for index, shipment in enumerate(self._shipments):
            card = _ShipmentCard(shipment, shipment.id == self._selected_id)
            card.clicked.connect(lambda _c=False, sid=shipment.id: self.select(sid))
            self._cards_layout.insertWidget(index, card)

    def _pill_style(self, active: bool, active_bg: str, active_fg: str, idle_fg: str) -> str:
        bg = active_bg if active else "transparent"
        fg = active_fg if active else idle_fg
        return (
            f"QPushButton {{ background-color: {bg}; color: {fg}; border: none; border-radius: 28px; "
            f"padding: 0 24px; font-size: 17px; font-weight: 700; }}"
        )

    def _render_detail(self) -> None:
        p = ORGANIC_PALETTE
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        shipment = self.selected()
        if shipment is None:
            self._from_label.setText("")
            name = self._dealer_name or "this dealership"
            self._title_label.setText(f"Nothing on its way to {name}")
            self._count_label.setText("")
            for widget in (self._accept_button, self._report_button, self._complete_button, self._note_input):
                widget.hide()
            return
        for widget in (self._accept_button, self._report_button, self._complete_button):
            widget.show()

        delivered = shipment.status == "delivered"
        report = self._report_mode.get(shipment.id, False) and not delivered
        accepted = (
            not report and not delivered
            and self._checked.get(shipment.id) == {l.product_barcode for l in shipment.lines}
            and all(self._received_for(shipment, l.product_barcode) == l.expected_qty for l in shipment.lines)
        )
        self._from_label.setText(f"{shipment.origin}" + (f" · Driver {shipment.driver}" if shipment.driver else "") +
                                 f" · {shipment.carrier}")
        self._title_label.setText(f"Shipment {shipment.number}")
        self._accept_button.setStyleSheet(self._pill_style(accepted, p["accent_2"], "white", "#3d472b"))
        self._report_button.setStyleSheet(self._pill_style(report, _YELLOW, "#3a2a05", "#8a5a00"))
        self._accept_button.setEnabled(not delivered)
        self._report_button.setEnabled(not delivered)
        self._note_input.setVisible(report)

        for index, line in enumerate(shipment.lines):
            self._rows_layout.insertWidget(index, self._build_row(shipment, line, report))

        checked = sum(1 for l in shipment.lines if self._is_checked(shipment, l.product_barcode))
        issues = sum(1 for l in shipment.lines if self._received_for(shipment, l.product_barcode) != l.expected_qty)
        issue_note = f" · {issues} discrepanc{'ies' if issues != 1 else 'y'}" if issues else ""
        self._count_label.setText(
            f"<b>{checked} of {len(shipment.lines)}</b> checked<span style='color:{_RED_TEXT};font-weight:700'>{issue_note}</span>"
        )
        ready = not delivered and checked == len(shipment.lines)
        self._complete_button.setText(  # "&&": a lone "&" is a keyboard-mnemonic marker
            "Receipt completed" if delivered else ("Send report && receive" if issues else "Complete receipt")
        )
        self._complete_button.setEnabled(ready)
        self._complete_button.setStyleSheet(
            f"QPushButton {{ background-color: {p['text_primary']}; color: {p['background']}; border: none; "
            f"border-radius: 30px; padding: 0 32px; font-size: 18px; font-weight: 700; }}"
            f"QPushButton:disabled {{ background-color: #8f8a82; color: #e9e3d8; }}"
        )

    def _build_row(self, shipment: Shipment, line, report: bool) -> QWidget:
        p = ORGANIC_PALETTE
        received = self._received_for(shipment, line.product_barcode)
        checked = self._is_checked(shipment, line.product_barcode)
        differs = received != line.expected_qty
        row = QWidget()
        row.setObjectName("receiveRow")
        row.setAttribute(Qt.WA_StyledBackground, True)
        row_bg = "#fbeecd" if differs else (p["background"] if checked else "transparent")
        row.setStyleSheet(f"#receiveRow {{ background-color: {row_bg}; border-radius: 24px; }}")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        check = QPushButton("✓" if checked else "")
        check.setCursor(Qt.PointingHandCursor)
        check.setStyleSheet(
            _round_button(48, p["accent_2"], "white") if checked
            else _round_button(48, "transparent", "white", "3px solid #b9b0a1")
        )
        check.clicked.connect(lambda _c=False, b=line.product_barcode: self.toggle_line(b))
        layout.addWidget(check)

        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel(line.product_name)
        name.setStyleSheet(f"font-weight: 700; font-size: 17px; color: {p['text_primary']};")
        sku = QLabel(line.product_barcode)
        sku.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']};")
        names.addWidget(name)
        names.addWidget(sku)
        layout.addLayout(names, stretch=1)

        expected = QLabel(str(line.expected_qty))
        expected.setFixedWidth(110)
        expected.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        expected.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {p['text_primary']};")
        layout.addWidget(expected)

        received_box = QHBoxLayout()
        received_box.setSpacing(6)
        received_box.addStretch(1)
        if report:
            minus = QPushButton("−")
            minus.setStyleSheet(_round_button(44, p["surface_raised"], p["text_primary"]))
            minus.clicked.connect(lambda _c=False, b=line.product_barcode: self.change_received(b, -1))
            received_box.addWidget(minus)
        value = QLabel(str(received))
        value.setMinimumWidth(44)
        value.setAlignment(Qt.AlignCenter)
        value.setStyleSheet(f"font-size: 20px; font-weight: 700; color: {_RED_TEXT if differs else p['text_primary']};")
        received_box.addWidget(value)
        if report:
            plus = QPushButton("+")
            plus.setStyleSheet(_round_button(44, p["surface_raised"], p["text_primary"]))
            plus.clicked.connect(lambda _c=False, b=line.product_barcode: self.change_received(b, 1))
            received_box.addWidget(plus)
        received_widget = QWidget()
        received_widget.setObjectName("clear")
        received_widget.setFixedWidth(190)
        received_widget.setLayout(received_box)
        layout.addWidget(received_widget)
        return row
