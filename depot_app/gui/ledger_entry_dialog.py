"""Record-a-ledger-document dialog for the depot's Local Treasury &
Ledger tab. Not in the mockup (its ledger tab only lists documents), but
without it nothing the depot receives or issues could ever get into its
own ledger - see treasury_panel.py's docstring. Same fields as
admin_app's LedgerEntryFormPopup, Industry-styled, with the site fixed
to this depot. Validation is repeated authoritatively by
database.ledger_repository.create()."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.formatting import MAX_AMOUNT
from shared.models import LEDGER_DOC_TYPES, LedgerEntry

# The depot mockup's own names for the four document types - its view of
# the usual direction (a transfer is a "Payment", an invoice a
# "Receivable"). Direction decides the real name: use depot_type_label().
DEPOT_TYPE_LABELS = {
    "check": "Check",
    "note": "Promissory note",
    "transfer": "Payment",
    "invoice": "Receivable",
}

_LABELS_BY_DIRECTION = {
    ("in", "check"): "Received check",
    ("out", "check"): "Issued check",
    ("in", "note"): "Received promissory note",
    ("out", "note"): "Issued promissory note",
    ("in", "transfer"): "Incoming transfer",
    ("out", "transfer"): "Payment",
    ("in", "invoice"): "Receivable",
    ("out", "invoice"): "Payable",
}


def depot_type_label(direction: str, doc_type: str) -> str:
    """The name for a document by direction AND type: an incoming invoice
    is a Receivable, an outgoing one a Payable; an outgoing transfer is a
    Payment, an incoming one an Incoming transfer."""
    return _LABELS_BY_DIRECTION.get((direction, doc_type), DEPOT_TYPE_LABELS.get(doc_type, doc_type))


def _date_edit(value: date) -> QDateEdit:
    edit = QDateEdit(QDate(value.year, value.month, value.day))
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd.MM.yyyy")
    return edit


class LedgerEntryDialog(QDialog):
    def __init__(self, site: str, counterparties: list[str], today: date, parent: QWidget | None = None):
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self._site = site
        self.setWindowTitle("Record ledger document")
        self.setStyleSheet(
            f"""
            QDialog {{ background-color: {p['background']}; }}
            QLabel {{ color: {p['text_primary']}; font-size: 13px; }}
            QLineEdit, QComboBox, QDateEdit, QDoubleSpinBox {{
                background-color: {p['surface_raised']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; border-radius: 0; padding: 6px 8px; font-size: 14px; }}
            """
        )

        title = QLabel(f"RECORD DOCUMENT · {site}")
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 20px; letter-spacing: 1px;"
        )

        self.direction_input = QComboBox()
        self.direction_input.addItem("Received (money coming in)", "in")
        self.direction_input.addItem("Issued (money going out)", "out")
        self.type_input = QComboBox()
        for key in LEDGER_DOC_TYPES:
            self.type_input.addItem(depot_type_label("in", key), key)
        self.direction_input.currentIndexChanged.connect(self._relabel_types)
        self.doc_no_input = QLineEdit()
        self.doc_no_input.setPlaceholderText("e.g. ÇK-004812")
        self.counterparty_input = QLineEdit()
        completer = QCompleter(list(counterparties), self.counterparty_input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.counterparty_input.setCompleter(completer)
        self.detail_input = QLineEdit()
        self.detail_input.setPlaceholderText("Bank or note term (optional)")
        self.issue_input = _date_edit(today)
        self.due_input = _date_edit(today)
        self.amount_input = QDoubleSpinBox()
        self.amount_input.setDecimals(2)
        self.amount_input.setRange(0, MAX_AMOUNT)  # same cap the repository enforces
        self.amount_input.setGroupSeparatorShown(True)

        for line_edit, limit in ((self.doc_no_input, 60), (self.counterparty_input, 120), (self.detail_input, 120)):
            line_edit.setMaxLength(limit)

        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.PlainText)  # repository messages echo typed text
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(
            f"color: {p['text_primary']}; background-color: #fff6d6; border: 1px solid #f4b400; "
            f"padding: 6px 10px; font-size: 12px;"
        )
        self.error_label.hide()

        form = QFormLayout(self)
        form.setContentsMargins(22, 18, 22, 18)
        form.setSpacing(10)
        form.addRow(title)
        form.addRow("Direction", self.direction_input)
        form.addRow("Type", self.type_input)
        form.addRow("Doc no.", self.doc_no_input)
        form.addRow("Counterparty", self.counterparty_input)
        form.addRow("Bank / detail", self.detail_input)
        form.addRow("Issue date", self.issue_input)
        form.addRow("Due date", self.due_input)
        form.addRow("Amount", self.amount_input)
        form.addRow(self.error_label)

        buttons = QHBoxLayout()
        cancel = IndustryButton("Cancel", variant="ghost")
        cancel.clicked.connect(self.reject)
        save = IndustryButton("Record", variant="accent")
        save.clicked.connect(self._validate_and_accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        form.addRow(buttons)

    def _relabel_types(self) -> None:
        """Type names follow the direction ("Receivable" vs "Payable")."""
        direction = self.direction_input.currentData()
        for index in range(self.type_input.count()):
            self.type_input.setItemText(index, depot_type_label(direction, self.type_input.itemData(index)))

    def _validate_and_accept(self) -> None:
        problems = []
        if not self.doc_no_input.text().strip():
            problems.append("Enter a document number.")
        if not self.counterparty_input.text().strip():
            problems.append("Enter a counterparty.")
        if self.amount_input.value() <= 0:
            problems.append("Enter an amount above 0.")
        if self.due_input.date() < self.issue_input.date():
            problems.append("The due date can't be before the issue date.")
        if problems:
            self.show_error(" ".join(problems))
            return
        self.accept()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def result_entry(self) -> LedgerEntry:
        def to_date(q: QDate) -> date:
            return date(q.year(), q.month(), q.day())

        return LedgerEntry(
            direction=self.direction_input.currentData(),
            doc_type=self.type_input.currentData(),
            doc_no=self.doc_no_input.text().strip(),
            counterparty=self.counterparty_input.text().strip(),
            detail=self.detail_input.text().strip() or None,
            site=self._site,
            issue_date=to_date(self.issue_input.date()),
            due_date=to_date(self.due_input.date()),
            amount=round(self.amount_input.value(), 2),
        )
