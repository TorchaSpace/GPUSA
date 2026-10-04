"""Offscreen GUI tests for admin_app's Treasury & Ledger page."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import ledger_repository
from tests.ledger_support import ACTOR, OTHER, SignedIn, session_for
from shared.models import LedgerEntry

TODAY = date(2026, 9, 24)

ledger = SignedIn(ledger_repository)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    from shared import current_session

    current_session.set(session_for(ACTOR, area="admin"))  # (conftest signs everyone out after each test)


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


# --- review fixes -------------------------------------------------------------


def test_a_database_error_shows_an_error_state_not_an_empty_ledger(page, monkeypatch):
    import sqlite3

    _add(doc_no="CHK-1")
    page.reload()
    assert page._table.rowCount() == 1

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    real = ledger.list_entries
    monkeypatch.setattr(ledger, "list_entries", boom)
    page.reload()

    assert "locked" in page.load_error()
    assert page._table.rowCount() == 0
    assert page._recv_card._value_label.text() == "—" and page._pay_card._value_label.text() == "—"
    assert "Couldn't load the ledger" in page._footer_label.text()
    assert "0 documents" not in page._footer_label.text()

    monkeypatch.setattr(ledger, "list_entries", real)
    page.reload()
    assert page.load_error() is None and page._table.rowCount() == 1
    assert "1 document" in page._footer_label.text()


def test_user_text_is_escaped_in_the_focus_line_and_due_soon_list(page):
    _add(doc_no="<i>X</i>", counterparty="<b>Evil</b> & Co", due=date(2026, 9, 26))
    page.reload()

    page._show_focus(2)
    text = page._focus_label.text()
    assert "<b>Evil</b>" not in text and "&lt;b&gt;Evil&lt;/b&gt; &amp; Co" in text
    assert "&lt;I&gt;X&lt;/I&gt;" in text  # doc numbers are stored upper-cased

    from PySide6.QtWidgets import QLabel

    labels = [l.text() for l in page._due_card.findChildren(QLabel)]
    assert any("&lt;b&gt;Evil&lt;/b&gt;" in t for t in labels)
    assert not any("<b>Evil</b>" in t for t in labels)


def test_html_title_in_focus_html_is_escaped(page):
    assert "<script>" not in page._focus_html("<script>", [])


def test_settled_documents_cant_be_edited_or_deleted_from_the_ui(page):
    entry = _add()
    ledger.mark_cleared(entry.id)
    page.reload()
    page.select_entry(entry.id)

    assert not page._edit_button.isEnabled() and not page._delete_button.isEnabled()
    page._open_edit()
    assert "Reopen" in page._message_label.text()

    page._change_status("reopen")
    assert page._edit_button.isEnabled() and page._delete_button.isEnabled()


def test_a_stale_clear_is_reported_and_the_page_shows_the_truth(page):
    entry = _add()
    page.reload()
    page.select_entry(entry.id)
    settled_at = ledger.mark_cleared(entry.id).settled_at  # another admin, first

    page._change_status("clear")

    assert "Couldn't update" in page._message_label.text()
    assert ledger.get(entry.id).settled_at == settled_at
    assert _texts(page, 6) == ["Cleared"]  # reloaded: no longer shows it as pending


def test_a_stale_delete_is_reported_not_raised(page, monkeypatch):
    entry = _add()
    page.reload()
    page.select_entry(entry.id)
    ledger.mark_cleared(entry.id)
    monkeypatch.setattr(page, "_confirm_delete", lambda e: True)

    page._delete_selected()

    assert "Couldn't delete" in page._message_label.text()
    assert len(ledger.list_entries()) == 1


def test_a_raw_sqlite_error_while_saving_is_shown_in_the_popup_not_raised(page, monkeypatch):
    import sqlite3

    page._open_record()
    popup = page._popup
    popup._doc_no_input.setText("CHK-9")
    popup._counterparty_input.setText("Harbor")
    popup._amount_input.setValue(5)

    def boom(entry):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "create", boom)
    page._save_popup()

    assert "locked" in popup._error.text()
    popup.close()


def test_popup_amount_range_matches_the_repository_cap(page):
    from shared.formatting import MAX_AMOUNT

    assert page._popup._amount_input.maximum() == MAX_AMOUNT


# --- audit trail: who / when / what -------------------------------------------------


def test_history_shows_who_did_what_for_the_selected_document(page):
    from shared import current_session

    entry = _add(doc_no="CHK-1")  # recorded by Murat
    page.reload()
    assert "Select a document" in page.history_text()

    page.select_entry(entry.id)
    text = page.history_text()
    assert "Check · CHK-1" in text and "Murat Yılmaz · B-100" in text and "recorded" in text

    current_session.set(session_for(OTHER))
    page._change_status("clear")
    text = page.history_text()
    assert "Ayşe Demir · B-200" in text and "marked cleared" in text
    assert "Status: Pending → Cleared" in text
    assert ledger.get(entry.id).settled_by == "Ayşe Demir · B-200"


def test_an_edit_is_shown_field_by_field_as_old_to_new(page, qapp):
    entry = _add(doc_no="CHK-1", amount=84200)
    page.reload()
    page.select_entry(entry.id)

    page._open_edit()
    page._popup._amount_input.setValue(90000)
    page._popup._counterparty_input.setText("Metro Heavy Parts")
    page._popup._validate_and_accept()
    pump(qapp)

    text = page.history_text()
    assert "edited" in text
    assert "Amount: 84,200.00 → 90,000.00" in text
    assert "Counterparty: Harbor Point → Metro Heavy Parts" in text
    assert "Document no." not in text  # unchanged fields are not listed


def test_a_legacy_document_says_nothing_was_recorded_instead_of_a_blank(page):
    entry = _add(doc_no="OLD-1")
    from database.connection import connection_scope

    with connection_scope() as conn:  # as if it were entered before auditing existed
        conn.execute("DROP TRIGGER trg_ledger_audit_no_delete")
        conn.execute("DELETE FROM ledger_audit")
    page.reload()
    page.select_entry(entry.id)
    assert "entered before changes were tracked" in page.history_text()


def test_recent_activity_lists_every_change_and_keeps_deleted_documents(page, monkeypatch):
    kept = _add(doc_no="CHK-1")
    gone = _add(doc_no="CHK-2")
    page.reload()
    assert "recorded" in page.activity_text()

    page.select_entry(gone.id)
    monkeypatch.setattr(page, "_confirm_delete", lambda e: True)
    page._delete_selected()

    text = page.activity_text()
    assert "deleted · Check · CHK-2" in text  # the entry is gone from the table, not from the trail
    assert "recorded · Check · CHK-1" in text
    assert text.index("deleted") < text.index("recorded")  # newest first
    assert [e.id for e in ledger.list_entries()] == [kept.id]


def test_nothing_changes_without_a_signed_in_person(page, monkeypatch):
    from shared import current_session

    entry = _add(doc_no="CHK-1")
    page.reload()
    page.select_entry(entry.id)
    current_session.clear()

    page._change_status("clear")
    assert "Sign in first" in page._message_label.text()
    assert ledger.get(entry.id).status == "pending"

    monkeypatch.setattr(page, "_confirm_delete", lambda e: pytest.fail("must refuse before asking"))
    page._delete_selected()
    assert len(ledger.list_entries()) == 1 and "Sign in first" in page._message_label.text()

    page._open_record()
    page._open_edit()
    assert not page._popup.isVisible()
    assert len(ledger_repository.list_audit(entry.id)) == 1  # still just the creation


def test_signing_out_while_the_form_is_open_refuses_the_save(page, qapp):
    from shared import current_session

    page._open_record()
    popup = page._popup
    popup._doc_no_input.setText("PN-1")
    popup._counterparty_input.setText("Someone")
    popup._amount_input.setValue(10)
    current_session.clear()
    popup._validate_and_accept()
    pump(qapp)

    assert "Sign in first" in popup._error.text()
    assert ledger.list_entries() == []
