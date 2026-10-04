"""Offscreen GUI tests for depot_app's Manager Portal ledger tab."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import ledger_repository
from tests.ledger_support import ACTOR, SignedIn, session_for
from shared.models import LedgerEntry

SITE = "WH-01 · Test"
TODAY = date(2026, 9, 24)

ledger = SignedIn(ledger_repository)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    from shared import current_session

    current_session.set(session_for(ACTOR, area="depot_console"))  # (conftest signs everyone out after each test)


def _add(doc_type, doc_no, direction, amount, due=date(2026, 9, 30), site=SITE):
    return ledger.create(
        LedgerEntry(direction=direction, doc_type=doc_type, doc_no=doc_no, counterparty="X",
                    issue_date=date(2026, 9, 1), due_date=due, amount=amount, site=site)
    )


@pytest.fixture
def panel(qapp):
    from depot_app.gui.treasury_panel import TreasuryPanel

    widget = TreasuryPanel(SITE, today_provider=lambda: TODAY)
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def test_only_this_sites_documents_and_signed_net(panel):
    _add("check", "ÇK-1", "out", 23040)
    _add("invoice", "FT-1", "in", 186400)
    _add("check", "ELSEWHERE", "in", 5, site="WH-02")
    panel.reload()

    assert sorted(e.doc_no for e in panel.shown_entries()) == ["FT-1", "ÇK-1"]
    assert panel._net_label.text() == "+163,360.00"


def test_cells_and_toggle_filter(panel):
    _add("invoice", "FT-1", "in", 100)
    _add("invoice", "FT-2", "in", 50, due=date(2026, 9, 20))
    cleared = _add("transfer", "HV-1", "out", 30)
    ledger.mark_cleared(cleared.id)
    panel.reload()

    assert panel._cells["invoice"].caption.text() == "RECEIVABLES · 2"
    assert panel._cells["invoice"].sub.text() == "2 open to collect"
    assert panel._cells["transfer"].sub.text() == "0 not yet settled"

    panel._toggle_cell("invoice")
    assert {e.doc_no for e in panel.shown_entries()} == {"FT-1", "FT-2"}
    statuses = {panel._table.item(r, 2).text(): panel._table.item(r, 6).text() for r in range(panel._table.rowCount())}
    assert statuses == {"FT-1": "Open", "FT-2": "Overdue"}
    panel._toggle_cell("invoice")
    assert len(panel.shown_entries()) == 3


def test_record_document_stamps_the_site_and_retries_on_error(panel, monkeypatch):
    _add("check", "ÇK-1", "in", 10)
    panel.reload()
    attempts = []

    def fake_run(dialog):
        attempts.append(1)
        if len(attempts) == 1:  # first try: a duplicate number
            dialog.doc_no_input.setText("ÇK-1")
        elif len(attempts) == 2:
            assert "already recorded" in dialog.error_label.text()
            dialog.doc_no_input.setText("ÇK-2")
        else:
            return False
        dialog.counterparty_input.setText("Ege Gıda")
        dialog.amount_input.setValue(64500)
        return True

    monkeypatch.setattr(panel, "_run_dialog", fake_run)
    panel._record()

    saved = next(e for e in ledger.list_entries() if e.doc_no == "ÇK-2")
    assert (saved.site, saved.amount, saved.direction) == (SITE.upper(), 64500, "in")  # sites are stored upper-cased
    assert "Recorded" in panel._message.text()
    assert len(attempts) == 2


def test_a_recorded_document_carries_who_recorded_it(panel, monkeypatch):
    def fake_run(dialog):
        dialog.doc_no_input.setText("ÇK-9")
        dialog.counterparty_input.setText("Ege Gıda")
        dialog.amount_input.setValue(100)
        return True

    monkeypatch.setattr(panel, "_run_dialog", fake_run)
    panel._record()

    saved = next(e for e in ledger.list_entries() if e.doc_no == "ÇK-9")
    assert saved.created_by == ACTOR.label
    [record] = ledger_repository.list_audit(saved.id)
    assert (record.action, record.actor_badge) == ("created", "B-100")


def test_recording_needs_a_signed_in_person(panel, monkeypatch):
    from shared import current_session

    current_session.clear()
    monkeypatch.setattr(panel, "_run_dialog", lambda dialog: pytest.fail("the dialog must not open"))
    panel._record()

    assert "Sign in first" in panel._message.text()
    assert ledger.list_entries() == []


def test_signing_out_while_the_dialog_is_open_keeps_it_open_with_the_reason(panel, monkeypatch):
    from shared import current_session

    shown = []

    def fake_run(dialog):
        if shown:
            assert "Sign in first" in dialog.error_label.text()
            return False
        shown.append(1)
        dialog.doc_no_input.setText("ÇK-9")
        dialog.counterparty_input.setText("Ege Gıda")
        dialog.amount_input.setValue(100)
        current_session.clear()
        return True

    monkeypatch.setattr(panel, "_run_dialog", fake_run)
    panel._record()
    assert ledger.list_entries() == []


# --- review fixes -------------------------------------------------------------


def _type_cells(panel):
    return {k: (c.caption.text(), c.total.text(), c.sub.text()) for k, c in panel._cells.items()}


def test_an_outgoing_invoice_is_a_payable_not_a_receivable(panel):
    _add("invoice", "FT-OUT", "out", 500)
    panel.reload()

    assert panel._cells["invoice"].caption.text() == "PAYABLES · 1"
    assert panel._cells["invoice"].total.text() == "−500.00"
    assert panel._cells["invoice"].sub.text() == "1 open to pay"
    assert panel._table.item(0, 1).text() == "Payable"
    assert panel._filter_buttons["invoice"].text() == "Payables"


def test_mixed_directions_are_split_not_abs_of_a_mixed_sum(panel):
    _add("invoice", "FT-IN", "in", 1000)
    _add("invoice", "FT-OUT", "out", 400)
    panel.reload()

    cell = panel._cells["invoice"]
    assert cell.caption.text() == "INVOICES · 2"
    assert cell.total.text() == "+1,000.00 / −400.00"  # never abs(+1000 - 400)
    assert cell.sub.text() == "1 open to collect · 1 open to pay"


def test_cell_totals_count_open_documents_only(panel):
    _add("check", "C-1", "in", 100)
    done = _add("check", "C-2", "in", 900)
    ledger.mark_cleared(done.id)
    _add("check", "C-3", "out", 30)
    panel.reload()

    caption, total, sub = _type_cells(panel)["check"]
    assert caption == "CHECKS · 3"
    assert total == "+100.00 / −30.00"
    assert sub == "2 not yet settled"


def test_a_cell_with_nothing_open_reads_zero(panel):
    done = _add("transfer", "HV-1", "out", 30)
    ledger.mark_cleared(done.id)
    panel.reload()
    assert panel._cells["transfer"].total.text() == "0.00"


def test_net_matches_admins_summarize_open_only_and_signs(panel):
    from shared.treasury import summarize

    _add("check", "A", "in", 100)
    _add("invoice", "B", "out", 250.5)
    settled = _add("transfer", "C", "out", 9999)
    ledger.mark_cleared(settled.id)
    panel.reload()

    expected = summarize(ledger.list_entries(site=SITE), TODAY).net_position  # -150.5
    assert expected == -150.5
    assert panel._net_label.text() == "−150.50"

    panel._toggle_cell("check")  # shown rows only: just the +100 check
    assert panel._net_label.text() == "+100.00"


def test_net_of_nothing_open_is_unsigned_zero(panel):
    done = _add("check", "A", "in", 100)
    ledger.mark_cleared(done.id)
    panel.reload()
    assert panel._net_label.text() == "0.00"


def test_type_labels_follow_direction():
    from depot_app.gui.ledger_entry_dialog import depot_type_label

    assert depot_type_label("in", "invoice") == "Receivable"
    assert depot_type_label("out", "invoice") == "Payable"
    assert depot_type_label("out", "transfer") == "Payment"
    assert depot_type_label("in", "transfer") == "Incoming transfer"
    assert depot_type_label("in", "check") == "Received check"
    assert depot_type_label("out", "note") == "Issued promissory note"


def test_dialog_type_names_follow_the_direction_picker(qapp):
    from depot_app.gui.ledger_entry_dialog import LedgerEntryDialog

    dialog = LedgerEntryDialog(SITE, [], TODAY)
    invoice = dialog.type_input.findData("invoice")
    assert dialog.type_input.itemText(invoice) == "Receivable"
    dialog.direction_input.setCurrentIndex(dialog.direction_input.findData("out"))
    assert dialog.type_input.itemText(invoice) == "Payable"
    from shared.formatting import MAX_AMOUNT

    assert dialog.amount_input.maximum() == MAX_AMOUNT
    dialog.close()


def test_site_label_is_escaped(qapp):
    from PySide6.QtWidgets import QLabel

    from depot_app.gui.treasury_panel import TreasuryPanel

    widget = TreasuryPanel("<i>WH</i>", today_provider=lambda: TODAY)
    texts = [l.text() for l in widget.findChildren(QLabel)]
    assert any("&lt;i&gt;WH&lt;/i&gt;" in t for t in texts)
    assert not any("<b><i>WH</i></b>" in t for t in texts)
    widget.close()


def test_a_failed_refresh_says_so_and_keeps_the_last_rows(panel, monkeypatch):
    import sqlite3

    _add("check", "A", "in", 100)
    panel.reload()

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger_repository, "list_entries", boom)
    panel.reload()

    assert "Couldn't refresh" in panel._message.text() and "locked" in panel._message.text()
    assert len(panel.shown_entries()) == 1


def test_a_raw_sqlite_error_on_save_is_shown_in_the_dialog_not_raised(panel, monkeypatch):
    import sqlite3

    attempts = []

    def fake_run(dialog):
        attempts.append(1)
        if len(attempts) == 2:
            assert "locked" in dialog.error_label.text()
            return False
        dialog.doc_no_input.setText("X-1")
        dialog.counterparty_input.setText("Y")
        dialog.amount_input.setValue(5)
        return True

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(panel, "_run_dialog", fake_run)
    monkeypatch.setattr(ledger_repository, "create", boom)
    panel._record()
    assert len(attempts) == 2
