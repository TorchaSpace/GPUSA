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
from database.exceptions import DataAccessError
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.ledger_entry_dialog import DEPOT_TYPE_LABELS, LedgerEntryDialog
from depot_app.theme import FONT_HEADING, INDUSTRY_PALETTE
from shared.formatting import format_amount
from shared.models import LedgerEntry
from shared.treasury import display_status, is_overdue

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
            f"font-family: '{FONT_HEADING}'; font-weight: 600; font-size: 28px; color: {p['text_primary']}; background: transparent;"
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

        scope = QLabel(f"Filtered to <b>{site}</b>")
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
        self._message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        outer.addWidget(self._message)

        footer = QHBoxLayout()
        footer_caption = QLabel(f"NET POSITION · {site} · SHOWN ROWS")
        footer_caption.setStyleSheet(f"font-size: 13px; letter-spacing: 1px; color: {p['text_secondary']};")
        self._net_label = QLabel()
        self._net_label.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 600; font-size: 32px; color: {p['text_primary']};"
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
        except Exception:  # a transient lock shouldn't blank the portal
            return
        # Newest document first, like the mockup's ledger.
        self._entries.sort(key=lambda e: (e.issue_date, e.id or 0), reverse=True)
        for key, label in _TYPE_CELLS:
            items = [e for e in self._entries if e.doc_type == key]
            open_count = sum(1 for e in items if e.is_open)
            total = abs(sum(e.signed_amount for e in items))
            cell = self._cells[key]
            cell.caption.setText(f"{label.upper()} · {len(items)}")
            cell.total.setText(format_amount(total))
            cell.sub.setText(f"{open_count} open to collect" if key == "invoice" else f"{open_count} not yet settled")
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
                DEPOT_TYPE_LABELS.get(entry.doc_type, entry.doc_type),
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
        net = sum(e.signed_amount for e in rows)
        self._net_label.setText(("+" if net > 0 else "−" if net < 0 else "") + format_amount(abs(net)))

    def shown_entries(self) -> list[LedgerEntry]:
        return list(self._shown)

    # --- recording ------------------------------------------------------

    def _run_dialog(self, dialog: LedgerEntryDialog) -> bool:
        """Separate so tests can drive the dialog without a modal loop."""
        return dialog.exec() == QDialog.Accepted

    def _record(self) -> None:
        try:
            names = ledger_repository.known_counterparties()
        except Exception:
            names = []
        dialog = LedgerEntryDialog(self._site, names, self.today(), self)
        while self._run_dialog(dialog):
            try:
                saved = ledger_repository.create(dialog.result_entry())
            except (DataAccessError, ValueError) as exc:
                dialog.show_error(f"Couldn't save: {exc}")
                continue
            self.reload()
            self._message.setText(
                f"Recorded {DEPOT_TYPE_LABELS[saved.doc_type].lower()} {saved.doc_no} · {signed_text(saved)}. "
                f"It's settled from Admin > Treasury & Ledger."
            )
            return
