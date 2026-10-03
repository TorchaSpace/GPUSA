"""Record/edit-a-ledger-document popup for the Treasury & Ledger page -
the mockup's "+ Record receipt" / "+ Record payment" button, which the
mockup itself never wires to a form. Same RefreshablePopup + QFormLayout
shape as dealership_form_popup.py; the fields are exactly the columns
the mockup's table shows (type + number, counterparty + bank/detail,
issue date, due date, amount) plus direction and site."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
)

from shared.i18n import enum_label, tr
from shared.formatting import MAX_AMOUNT
from shared.gui_kit.popup_window import RefreshablePopup
from shared.models import LEDGER_DOC_TYPES, LedgerEntry

_DIRECTIONS = ("in", "out")


def _date_edit() -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd.MM.yyyy")
    return edit


def _to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _from_qdate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class LedgerEntryFormPopup(RefreshablePopup):
    """Emits QDialog's `accepted` once the fields validate; the host
    reads result_entry() and is_editing()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing: LedgerEntry | None = None

        self._direction_input = QComboBox()
        for key in _DIRECTIONS:
            self._direction_input.addItem(tr(f"admin.treasury.f_dir_{key}"), key)
        self._type_input = QComboBox()
        for key in LEDGER_DOC_TYPES:
            self._type_input.addItem(enum_label("doc_type", key), key)
        self._doc_no_input = QLineEdit()
        self._doc_no_input.setPlaceholderText(tr("admin.treasury.f_doc_no_ph"))
        self._counterparty_input = QLineEdit()
        self._detail_input = QLineEdit()
        self._detail_input.setPlaceholderText(tr("admin.treasury.f_detail_ph"))
        self._site_input = QLineEdit()
        self._site_input.setPlaceholderText(tr("admin.treasury.f_site_ph"))
        self._issue_input = _date_edit()
        self._due_input = _date_edit()
        self._amount_input = QDoubleSpinBox()
        self._amount_input.setDecimals(2)
        self._amount_input.setRange(0, MAX_AMOUNT)  # same cap the repository enforces
        self._amount_input.setGroupSeparatorShown(True)
        for line_edit, limit in (
            (self._doc_no_input, 60), (self._counterparty_input, 120), (self._detail_input, 120), (self._site_input, 60),
        ):
            line_edit.setMaxLength(limit)
        self._error = QLabel()
        self._error.setTextFormat(Qt.PlainText)  # repository messages echo typed text
        self._error.setWordWrap(True)
        self._error.hide()

        form = QFormLayout()
        form.addRow(tr("admin.treasury.f_direction"), self._direction_input)
        form.addRow(tr("admin.treasury.f_type"), self._type_input)
        form.addRow(tr("admin.treasury.f_doc_no"), self._doc_no_input)
        form.addRow(tr("admin.treasury.f_counterparty"), self._counterparty_input)
        form.addRow(tr("admin.treasury.f_detail"), self._detail_input)
        form.addRow(tr("admin.treasury.f_site"), self._site_input)
        form.addRow(tr("admin.treasury.f_issue"), self._issue_input)
        form.addRow(tr("admin.treasury.f_due"), self._due_input)
        form.addRow(tr("admin.treasury.f_amount"), self._amount_input)
        form.addRow(self._error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def refresh_content(
        self,
        entry: LedgerEntry | None = None,
        direction: str = "in",
        counterparties: list[str] = (),
        sites: list[str] = (),
        today: date | None = None,
    ) -> None:
        today = today or date.today()
        self._editing = entry
        self._error.hide()
        self.setWindowTitle(
            tr("admin.treasury.f_title_edit") if entry
            else (tr("admin.treasury.f_title_receipt") if direction == "in" else tr("admin.treasury.f_title_payment"))
        )

        for widget, names in ((self._counterparty_input, counterparties), (self._site_input, sites)):
            completer = QCompleter(list(names), widget)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            widget.setCompleter(completer)

        source = entry
        self._direction_input.setCurrentIndex(self._direction_input.findData(source.direction if source else direction))
        self._type_input.setCurrentIndex(self._type_input.findData(source.doc_type if source else "check"))
        self._doc_no_input.setText(source.doc_no if source else "")
        self._counterparty_input.setText(source.counterparty if source else "")
        self._detail_input.setText((source.detail or "") if source else "")
        self._site_input.setText((source.site or "") if source else "")
        self._issue_input.setDate(_to_qdate(source.issue_date if source else today))
        self._due_input.setDate(_to_qdate(source.due_date if source else today))
        self._amount_input.setValue(source.amount if source else 0)

    def is_editing(self) -> bool:
        return self._editing is not None

    def _validate_and_accept(self) -> None:
        problems = []
        if not self._doc_no_input.text().strip():
            problems.append(tr("admin.treasury.f_err_docno"))
        if not self._counterparty_input.text().strip():
            problems.append(tr("admin.treasury.f_err_cp"))
        if self._amount_input.value() <= 0:
            problems.append(tr("admin.treasury.f_err_amount"))
        if self._due_input.date() < self._issue_input.date():
            problems.append(tr("admin.treasury.f_err_dates"))
        if problems:
            self._error.setText(" ".join(problems))
            self._error.show()
            return
        self.accept()

    def show_error(self, message: str) -> None:
        """For the host to report a save failure (e.g. a duplicate number)
        without losing what was typed."""
        self._error.setText(message)
        self._error.show()
        self.open_or_refresh_keep()

    def open_or_refresh_keep(self) -> None:
        if not self.isVisible():
            self.show()
        self.raise_()

    def result_entry(self) -> LedgerEntry:
        base = self._editing
        return LedgerEntry(
            id=base.id if base else None,
            status=base.status if base else "pending",
            direction=self._direction_input.currentData(),
            doc_type=self._type_input.currentData(),
            doc_no=self._doc_no_input.text().strip(),
            counterparty=self._counterparty_input.text().strip(),
            detail=self._detail_input.text().strip() or None,
            site=self._site_input.text().strip() or None,
            issue_date=_from_qdate(self._issue_input.date()),
            due_date=_from_qdate(self._due_input.date()),
            amount=round(self._amount_input.value(), 2),
        )
