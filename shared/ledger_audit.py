"""Reading the ledger's audit trail as people do (no Qt, no SQL): which
fields an edit changed, and the "who / when / what" wording for one
database.ledger_repository audit record.

The records themselves (shared.models.LedgerAuditRecord) hold the entry's
fields before and after each change as plain dicts; this module turns a
pair of those into "Amount: 84,200.00 → 90,000.00" lines, in the language
the app runs in.
"""

from __future__ import annotations

from datetime import date

from shared.formatting import day_month_text, format_amount, local_datetime_text
from shared.i18n import enum_label, tr
from shared.models import LEDGER_DOC_TYPE_LABELS, LedgerAuditRecord

# Fields an edit can change, in the order the entry form shows them. status
# is included (a clear/endorse/reopen shows as "Status: Pending → Cleared");
# settled_at is bookkeeping and is left out.
DIFF_FIELDS = (
    "direction", "doc_type", "doc_no", "counterparty", "detail", "site",
    "issue_date", "due_date", "amount", "status",
)

_FIELD_LABEL_KEYS = {
    "direction": "admin.treasury.f_direction",
    "doc_type": "admin.treasury.f_type",
    "doc_no": "admin.treasury.f_doc_no",
    "counterparty": "admin.treasury.f_counterparty",
    "detail": "admin.treasury.f_detail",
    "site": "admin.treasury.f_site",
    "issue_date": "admin.treasury.f_issue",
    "due_date": "admin.treasury.f_due",
    "amount": "admin.treasury.f_amount",
    "status": "admin.treasury.col_status",
}

_EMPTY = "—"


def changed_fields(before: dict | None, after: dict | None) -> list[tuple[str, object, object]]:
    """(field, old, new) for every field that differs, in form order. A
    creation (no `before`) or a deletion (no `after`) has no diff - the
    record itself says what happened."""
    if not before or not after:
        return []
    return [(f, before.get(f), after.get(f)) for f in DIFF_FIELDS if before.get(f) != after.get(f)]


def field_label(field: str) -> str:
    key = _FIELD_LABEL_KEYS.get(field)
    return tr(key) if key else field


def value_text(field: str, value) -> str:
    """One stored value as shown: amounts with separators, dates as
    "25 Sep 2026", codes as their translated names, blank as a dash."""
    if value is None or value == "":
        return tr("admin.treasury.company") if field == "site" else _EMPTY
    if field == "amount":
        return format_amount(float(value))
    if field in ("issue_date", "due_date"):
        try:
            moment = date.fromisoformat(str(value))
        except ValueError:
            return str(value)
        return f"{day_month_text(moment)} {moment.year}"
    if field == "direction":
        return tr("admin.treasury.audit_dir_in") if value == "in" else tr("admin.treasury.audit_dir_out")
    if field == "doc_type":
        label = enum_label("doc_type", str(value))
        return LEDGER_DOC_TYPE_LABELS.get(str(value), str(value)) if label == value else label
    if field == "status":
        shown = {"pending": "Pending", "cleared": "Cleared", "endorsed": "Endorsed"}.get(str(value))
        return enum_label("ledger_display", shown) if shown else str(value)
    return str(value)


def change_lines(record: LedgerAuditRecord) -> list[str]:
    """Readable "Field: old → new" lines for an edit (or any record that
    carries both snapshots); [] for a creation or deletion."""
    return [
        f"{field_label(f)}: {value_text(f, old)} → {value_text(f, new)}"
        for f, old, new in changed_fields(record.before, record.after)
    ]


def action_label(action: str) -> str:
    label = enum_label("ledger_audit_action", action)
    return label if label != action else action.replace("_", " ")


def subject_text(record: LedgerAuditRecord) -> str:
    """"Check · CHK-40211", from the entry's own snapshot (so it still
    works once the entry has been deleted)."""
    snap = record.after or record.before or {}
    doc_no = snap.get("doc_no") or f"#{record.entry_id}"
    doc_type = snap.get("doc_type")
    if not doc_type:
        return str(doc_no)
    return f"{value_text('doc_type', doc_type)} · {doc_no}"


def when_text(record: LedgerAuditRecord) -> str:
    return local_datetime_text(record.at)


def summary_line(record: LedgerAuditRecord, with_subject: bool = False) -> str:
    """"24.09.2026 14:05 · Murat Yılmaz · B-100 · edited" (+ " · Check · CHK-1")."""
    parts = [when_text(record), record.actor_label, action_label(record.action)]
    if with_subject:
        parts.append(subject_text(record))
    return " · ".join(parts)
