"""Offscreen GUI tests for depot_app's Manager Portal ledger tab."""

from __future__ import annotations

from datetime import date

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import ledger_repository as ledger
from shared.models import LedgerEntry

SITE = "WH-01 · Test"
TODAY = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


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
    assert (saved.site, saved.amount, saved.direction) == (SITE, 64500, "in")
    assert "Recorded" in panel._message.text()
    assert len(attempts) == 2
