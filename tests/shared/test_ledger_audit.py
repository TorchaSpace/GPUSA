"""shared.ledger_audit - turning audit records into readable lines."""

from __future__ import annotations

from shared import i18n, ledger_audit
from shared.models import LedgerAuditRecord

BEFORE = {
    "direction": "in", "doc_type": "check", "doc_no": "CHK-1", "counterparty": "Harbor Point",
    "detail": None, "site": None, "issue_date": "2026-09-02", "due_date": "2026-09-26",
    "amount": 84200.0, "status": "pending", "settled_at": None,
}


def _record(action="edited", before=BEFORE, after=None, **kw) -> LedgerAuditRecord:
    fields = dict(id=1, entry_id=7, at="2026-09-24T11:05:00.000Z", actor_badge="B-100",
                  actor_name="Murat Yılmaz", action=action, before=before, after=after)
    fields.update(kw)
    return LedgerAuditRecord(**fields)


def test_only_changed_fields_are_listed_in_form_order():
    after = dict(BEFORE, amount=90000.0, counterparty="Metro Parts", due_date="2026-10-01")
    assert ledger_audit.changed_fields(BEFORE, after) == [
        ("counterparty", "Harbor Point", "Metro Parts"),
        ("due_date", "2026-09-26", "2026-10-01"),
        ("amount", 84200.0, 90000.0),
    ]
    assert ledger_audit.changed_fields(BEFORE, dict(BEFORE)) == []


def test_creation_and_deletion_have_no_diff():
    assert ledger_audit.changed_fields(None, BEFORE) == []
    assert ledger_audit.changed_fields(BEFORE, None) == []
    assert ledger_audit.change_lines(_record("created", before=None, after=BEFORE)) == []
    assert ledger_audit.change_lines(_record("deleted", before=BEFORE, after=None)) == []


def test_change_lines_read_old_arrow_new():
    after = dict(BEFORE, amount=90000.0, detail="First Coastal Bank", site="WH-01", direction="out")
    lines = ledger_audit.change_lines(_record(after=after))
    assert lines == [
        "Direction: Received → Issued",
        "Bank / detail: — → First Coastal Bank",
        "Site: Company → WH-01",
        "Amount: 84,200.00 → 90,000.00",
    ]


def test_a_settlement_shows_as_a_status_change_and_settled_at_is_left_out():
    after = dict(BEFORE, status="cleared", settled_at="2026-09-24T11:05:00.000Z")
    assert ledger_audit.change_lines(_record("cleared", after=after)) == ["Status: Pending → Cleared"]


def test_summary_line_and_subject():
    record = _record("deleted", before=BEFORE, after=None)
    assert ledger_audit.subject_text(record) == "Check · CHK-1"  # from the snapshot: the entry itself is gone
    line = ledger_audit.summary_line(record, with_subject=True)
    assert "Murat Yılmaz · B-100" in line and line.endswith("deleted · Check · CHK-1")
    assert ledger_audit.summary_line(record).endswith("deleted")
    assert ledger_audit.subject_text(_record(before=None, after=None)) == "#7"


def test_turkish_wording():
    try:
        i18n.set_language("tr")
        assert ledger_audit.action_label("cleared") == "kapattı"
        after = dict(BEFORE, amount=90000.0, direction="out", status="endorsed")
        lines = ledger_audit.change_lines(_record(after=after))
        assert lines[0] == "Yön: Alınan → Verilen"
        assert "Tutar: 84.200,00 → 90.000,00" in lines
        assert "Durum: Bekliyor → Ciro edildi" in lines
        assert ledger_audit.subject_text(_record()) == "Çek · CHK-1"
    finally:
        i18n.set_language("en")
