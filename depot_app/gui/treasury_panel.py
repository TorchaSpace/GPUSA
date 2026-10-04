"""Local Treasury & Ledger - the real build of the Warehouse Console
mockup's Manager Portal "02 Local Treasury & Ledger" tab (portalTab
'ledger'), replacing its themed placeholder.

Recreated from the mockup:
- Four type cells - Checks, Promissory Notes, Payments, Receivables -
  each "LABEL · count", the total, and a line underneath ("N open to
  collect" / "N not yet settled"); clicking one filters the table to it,
  clicking it again clears the filter.
- The All / Checks / Promissory Notes / Payments / Receivables segmented
  filter and "Filtered to WH-01 · İstanbul Merkez".
- The table: Date, Type, Doc no., Counterparty, Due, Amount (signed:
  money in positive, money out negative), Status (Overdue as the
  mockup's inverted black tag).
- "Net position · WH-01 · shown rows" footer.

Real: database.ledger_repository, filtered to this depot's site; statuses
and totals from shared.treasury, the same functions admin_app's Treasury
page uses. The depot mockup's type names map onto the shared ones:
"Payment" = an outgoing transfer, "Receivable" = an incoming invoice.

Added beyond the mockup: "+ Record document", so a check or invoice
that lands at this depot can be entered here (stamped with this site)
instead of only at head office. Settling documents - cleared, endorsed,
reopened - is left to admin_app's Treasury page on purpose: that's where
the whole company's receivables/payables are reconciled.
"""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import ledger_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.ledger_entry_dialog import LedgerEntryDialog, depot_type_label
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared import current_session
from shared.formatting import format_amount
from shared.i18n import UserError, tr
from shared.models import LedgerEntry
from shared.treasury import display_status, is_overdue, summarize

# Cell order and plural labels exactly as the mockup's `types` map.
_TYPE_CELLS = (
    ("check", "Checks"),
    ("note", "Promissory Notes"),
    ("transfer", "Payments"),
    ("invoice", "Receivables"),
)


def _short_date(value: date) -> str:
    """The mockup's "22.09"."""
    return f"{value.day:02d}.{value.month:02d}"


def signed_text(entry: LedgerEntry) -> str:
    return ("+" if entry.direction == "in" else "−") + format_amount(entry.amount)


def signed_net_text(net: float) -> str:
    """A signed position: "+1,200.00", "−30.00", "0.00" - the sign is the
    direction, the number is never abs() of a mixed-sign sum."""
    return ("+" if net > 0 else "−" if net < 0 else "") + format_amount(abs(net))


def type_caption(doc_type: str, entries: list[LedgerEntry]) -> str:
    """Cell / filter caption for a document type, named by the directions
    actually present: invoices are Receivables when incoming, Payables
    when outgoing, plain Invoices when both; transfers likewise."""
    directions = {e.direction for e in entries}
    if doc_type == "invoice":
        return {frozenset({"out"}): "Payables", frozenset({"in", "out"}): "Invoices"}.get(
            frozenset(directions), "Receivables"
        )
    if doc_type == "transfer":
        return {frozenset({"in"}): "Incoming transfers", frozenset({"in", "out"}): "Transfers"}.get(
            frozenset(directions), "Payments"
        )
    return dict(_TYPE_CELLS)[doc_type]


def cell_total_text(entries: list[LedgerEntry], today: date) -> str:
    """OPEN documents only, split by direction as shared.treasury.summarize
    counts them (so these agree with Admin): "+X" for money coming in,
    "−Y" for money going out, "+X / −Y" when both are open."""
    summary = summarize(entries, today)
    parts = []
    if summary.receivables_total:
        parts.append(f"+{format_amount(summary.receivables_total)}")
    if summary.payables_total:
        parts.append(f"−{format_amount(summary.payables_total)}")
    return " / ".join(parts) or format_amount(0)


class _TypeCell(QPushButton):
    """One of the four clickable summary cells."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        p = INDUSTRY_PALETTE
        self.caption = QLabel()
        self.caption.setStyleSheet(f"font-size: 12px; letter-spacing: 1px; color: {p['text_secondary']}; background: transparent;")
        self.total = QLabel()
        self.total.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; color: {p['text_primary']}; background: transparent;"
        )
        self.sub = QLabel()
        self.sub.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']}; background: transparent;")
        for label in (self.caption, self.total, self.sub):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        self.setStyleSheet(
            f"""
            QPushButton {{ text-align: left; background-color: {p['background']};
                border: 1px solid {p['border']}; border-radius: 0; }}
            QPushButton:checked {{ background-color: {p['accent_100']}; border-color: {p['accent']}; }}
            QPushButton:hover {{ border-color: {p['text_secondary']}; }}
            """
        )


class TreasuryPanel(QWidget):
    def __init__(self, site: str, parent: QWidget | None = None, today_provider=date.today):
        super().__init__(parent)
        self._site = site
        self._today_provider = today_provider
        self._entries: list[LedgerEntry] = []
        self._shown: list[LedgerEntry] = []
        self._filter: str | None = None  # None = all types
        p = INDUSTRY_PALETTE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(18)

        cells_row = QHBoxLayout()
        cells_row.setSpacing(0)
        self._cells: dict[str, _TypeCell] = {}
        for key, _label in _TYPE_CELLS:
            cell = _TypeCell()
            cell.clicked.connect(lambda _c=False, k=key: self._toggle_cell(k))
            self._cells[key] = cell
            cells_row.addWidget(cell, stretch=1)
        outer.addLayout(cells_row)

        filters_row = QHBoxLayout()
        filters_row.setSpacing(0)
        self._filter_buttons: dict[str | None, QPushButton] = {}
        for key, label in ((None, "All"),) + _TYPE_CELLS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(40)
            button.setStyleSheet(
                f"""
                QPushButton {{ background-color: transparent; color: {p['text_primary']};
                    border: 1px solid {p['border']}; border-radius: 0; padding: 0 14px;
                    font-size: 14px; font-weight: 600; }}
                QPushButton:checked {{ background-color: {p['text_primary']}; color: {p['background']};
                    border-color: {p['text_primary']}; }}
                """
            )
            button.clicked.connect(lambda _c=False, k=key: self.set_filter(k))
            self._filter_buttons[key] = button
            filters_row.addWidget(button)
        filters_row.addStretch(1)
        record = IndustryButton("+ Record document", variant="ghost")
        record.clicked.connect(self._record)
        filters_row.addWidget(record)
        outer.addLayout(filters_row)

        scope = QLabel(f"Filtered to <b>{html.escape(site)}</b>")
        scope.setTextFormat(Qt.RichText)
        scope.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']};")
        outer.addWidget(scope, alignment=Qt.AlignRight)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(["Date", "Type", "Doc no.", "Counterparty", "Due", "Amount", "Status"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        header = self._table.horizontalHeader()
        for column in (0, 1, 2, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.setMinimumHeight(260)
        self._table.setStyleSheet(
            f"""
            QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; gridline-color: {p['border']}; font-size: 14px; }}
            QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
                border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
            """
        )
        outer.addWidget(self._table)

        self._message = QLabel()
        self._message.setTextFormat(Qt.PlainText)  # shows document numbers and error text
        self._message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        outer.addWidget(self._message)

        footer = QHBoxLayout()
        footer_caption = QLabel(f"OPEN NET POSITION · {site} · SHOWN ROWS")
        footer_caption.setTextFormat(Qt.PlainText)
        footer_caption.setStyleSheet(f"font-size: 13px; letter-spacing: 1px; color: {p['text_secondary']};")
        self._net_label = QLabel()
        self._net_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 32px; color: {p['text_primary']};"
        )
        footer.addWidget(footer_caption)
        footer.addStretch(1)
        footer.addWidget(self._net_label)
        rule = QWidget()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color: {p['border']};")
        outer.addWidget(rule)
        outer.addLayout(footer)

        self._filter_buttons[None].setChecked(True)
        self.reload()

    def today(self) -> date:
        return self._today_provider()

    # --- data ---------------------------------------------------------

    def reload(self) -> None:
        try:
            self._entries = ledger_repository.list_entries(site=self._site)
        except (ValueError, *DATABASE_ERRORS) as exc:
            # Keep what's showing (a transient lock shouldn't blank the
            # portal) but say it may be out of date.
            self._message.setText(f"Couldn't refresh the ledger - showing the last data. ({exc})")
            return
        # Newest document first, like the mockup's ledger.
        self._entries.sort(key=lambda e: (e.issue_date, e.id or 0), reverse=True)
        today = self.today()
        for key, _label in _TYPE_CELLS:
            items = [e for e in self._entries if e.doc_type == key]
            open_in = sum(1 for e in items if e.is_open and e.direction == "in")
            open_out = sum(1 for e in items if e.is_open and e.direction == "out")
            label = type_caption(key, items)
            cell = self._cells[key]
            cell.caption.setText(f"{label.upper()} · {len(items)}")
            cell.total.setText(cell_total_text(items, today))
            if key == "invoice":
                parts = ([f"{open_in} open to collect"] if open_in or not open_out else []) + (
                    [f"{open_out} open to pay"] if open_out else []
                )
                cell.sub.setText(" · ".join(parts))
            else:
                cell.sub.setText(f"{open_in + open_out} not yet settled")
            self._filter_buttons[key].setText(label)
        self._render()

    def set_filter(self, key: str | None) -> None:
        self._filter = key
        for button_key, button in self._filter_buttons.items():
            button.setChecked(button_key == key)
        for cell_key, cell in self._cells.items():
            cell.setChecked(cell_key == key)
        self._render()

    def _toggle_cell(self, key: str) -> None:
        self.set_filter(None if self._filter == key else key)

    def _render(self) -> None:
        p = INDUSTRY_PALETTE
        today = self.today()
        rows = [e for e in self._entries if self._filter is None or e.doc_type == self._filter]
        self._shown = rows
        self._table.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            status = display_status(entry, today)
            values = [
                _short_date(entry.issue_date),
                depot_type_label(entry.direction, entry.doc_type),
                entry.doc_no,
                entry.counterparty,
                _short_date(entry.due_date),
                signed_text(entry),
                status,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                font = item.font()
                if column == 0:
                    item.setForeground(QColor(p["text_secondary"]))
                if column == 2:
                    font.setBold(True)
                if column == 4 and is_overdue(entry, today):
                    font.setBold(True)
                if column == 5:
                    font.setBold(True)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(QColor(p["alert_success"] if entry.direction == "in" else p["text_primary"]))
                if column == 6:
                    if status == "Overdue":
                        item.setBackground(QColor(p["text_primary"]))
                        item.setForeground(QColor(p["background"]))
                        font.setBold(True)
                    elif status in ("Cleared", "Endorsed"):
                        item.setForeground(QColor(p["accent"]))
                    else:
                        item.setForeground(QColor(p["text_secondary"]))
                item.setFont(font)
                self._table.setItem(index, column, item)
        # Open documents only, receivables minus payables - the same
        # arithmetic as Admin's net position (shared.treasury.summarize).
        self._net_label.setText(signed_net_text(summarize(rows, today).net_position))

    def shown_entries(self) -> list[LedgerEntry]:
        return list(self._shown)

    # --- recording ------------------------------------------------------

    def _run_dialog(self, dialog: LedgerEntryDialog) -> bool:
        """Separate so tests can drive the dialog without a modal loop."""
        return dialog.exec() == QDialog.Accepted

    def _record(self) -> None:
        if current_session.actor() is None:  # every ledger change is recorded under a name
            self._message.setText(tr("err.ledger_actor_required"))
            return
        try:
            names = ledger_repository.known_counterparties()
        except (ValueError, *DATABASE_ERRORS):
            names = []
        dialog = LedgerEntryDialog(self._site, names, self.today(), self)
        while self._run_dialog(dialog):
            try:
                actor = current_session.actor()
                if actor is None:  # signed out while the dialog was open
                    raise UserError("err.ledger_actor_required")
                saved = ledger_repository.create(dialog.result_entry(), actor)
            except (ValueError, *DATABASE_ERRORS) as exc:
                dialog.show_error(f"Couldn't save: {exc}")
                continue
            self.reload()
            self._message.setText(
                f"Recorded {depot_type_label(saved.direction, saved.doc_type).lower()} {saved.doc_no} · {signed_text(saved)}. "
                f"It's settled from Admin > Treasury & Ledger."
            )
            return
