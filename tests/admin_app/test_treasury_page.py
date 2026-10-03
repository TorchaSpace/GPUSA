"""Offscreen GUI tests for admin_app's Treasury & Ledger page."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import ledger_repository as ledger
from shared.models import LedgerEntry

TODAY = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _add(direction="in", doc_type="check", doc_no="CHK-1", due=date(2026, 9, 26), amount=84200, **kw):
    return ledger.create(
        LedgerEntry(direction=direction, doc_type=doc_type, doc_no=doc_no, counterparty=kw.pop("counterparty", "Harbor Point"),
                    issue_date=date(2026, 9, 1), due_date=due, amount=amount, **kw)
    )


@pytest.fixture
def page(qapp):
    from admin_app.gui.pages.treasury_page import TreasuryPage

    widget = TreasuryPage(today_provider=lambda: TODAY)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _texts(page, column):
    return [page._table.item(r, column).text() for r in range(page._table.rowCount())]


def test_kpis_tabs_and_overdue(page):
    _add(doc_no="CHK-1", due=date(2026, 9, 26), amount=84200)
    _add(doc_no="CHK-2", due=date(2026, 9, 18), amount=46750)  # overdue
    _add(direction="out", doc_type="transfer", doc_no="WIR-1", due=date(2026, 9, 30), amount=186400)
    page.reload()

    assert page._recv_card._value_label.text() == "130.9k"  # 84,200 + 46,750
    assert page._pay_card._value_label.text() == "186.4k"
    assert page._due_card._value_label.text() == "2"  # CHK-1 and WIR-1; the overdue one isn't "due soon"
    assert _texts(page, 6) == ["Overdue", "Pending"]
    assert "6 days overdue" in _texts(page, 4)[0]
    assert page._tab_buttons["in"].text().endswith("2")

    page._set_tab("out")
    assert _texts(page, 0) == ["Transfer · WIR-1"]
    assert page._record_button.text() == "+ Record payment"


def test_status_filter_and_sort(page):
    _add(doc_no="A", due=date(2026, 9, 18))
    _add(doc_no="B", due=date(2026, 10, 1))
    cleared = _add(doc_no="C", due=date(2026, 9, 25))
    ledger.mark_cleared(cleared.id)
    page.reload()

    page._set_status_filter("Overdue")
    assert _texts(page, 0) == ["Check · A"]
    page._set_status_filter("Cleared")
    assert _texts(page, 0) == ["Check · C"]
    page._set_status_filter("All")
    page._on_header_clicked(4)  # due date descending
    assert _texts(page, 0) == ["Check · B", "Check · C", "Check · A"]


def test_record_through_the_popup(page, qapp):
    page._open_record()
    popup = page._popup
    popup._doc_no_input.setText("PN-1187")
    popup._type_input.setCurrentIndex(popup._type_input.findData("note"))
    popup._counterparty_input.setText("Metro Heavy Parts")
    popup._amount_input.setValue(212000)
    popup._due_input.setDate(popup._due_input.date().addDays(5))
    popup._validate_and_accept()
    pump(qapp)

    [entry] = ledger.list_entries()
    assert (entry.doc_type, entry.direction, entry.amount, entry.site) == ("note", "in", 212000, None)
    assert entry.issue_date == TODAY
    assert _texts(page, 0) == ["Promissory note · PN-1187"]


def test_popup_refuses_missing_fields(page, qapp):
    page._open_record()
    page._popup._validate_and_accept()
    assert page._popup.isVisible()
    assert "document number" in page._popup._error.text()
    assert ledger.list_entries() == []


def test_duplicate_number_keeps_the_popup_open_with_the_error(page, qapp):
    _add(doc_no="CHK-1")
    page.reload()
    page._open_record()
    popup = page._popup
    popup._doc_no_input.setText("CHK-1")
    popup._counterparty_input.setText("Someone")
    popup._amount_input.setValue(10)
    popup._validate_and_accept()
    pump(qapp)

    assert "already recorded" in popup._error.text()
    assert len(ledger.list_entries()) == 1


def test_clear_endorse_reopen_from_the_selection(page):
    entry = _add(doc_no="CHK-1")
    page.reload()
    page.select_entry(entry.id)
    assert page._endorse_button.isEnabled() and not page._reopen_button.isEnabled()

    page._change_status("endorse")
    assert ledger.get(entry.id).status == "endorsed"
    assert page._reopen_button.isEnabled() and not page._clear_button.isEnabled()

    page._change_status("reopen")
    page._change_status("clear")
    assert ledger.get(entry.id).status == "cleared"


def test_outgoing_documents_cannot_be_endorsed_from_the_ui(page):
    entry = _add(direction="out", doc_no="OUT-1")
    page.reload()
    page._set_tab("out")
    page.select_entry(entry.id)
    assert not page._endorse_button.isEnabled()


def test_delete_asks_first(page, monkeypatch):
    entry = _add()
    page.reload()
    page.select_entry(entry.id)
    monkeypatch.setattr(page, "_confirm_delete", lambda e: False)
    page._delete_selected()
    assert len(ledger.list_entries()) == 1

    monkeypatch.setattr(page, "_confirm_delete", lambda e: True)
    page._delete_selected()
    assert ledger.list_entries() == []


def test_milestone_strip_and_focus(page):
    _add(doc_no="CHK-1", due=date(2026, 9, 26), amount=84200)
    page.reload()

    days = page._strip.days()
    assert len(days) == 30 and days[2].day == date(2026, 9, 26)
    page._show_focus(2)
    assert "Harbor Point" in page._focus_label.text()
    assert "84.2k" in page._net_end_label.text()
