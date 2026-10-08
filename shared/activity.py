"""Turning a feed event (database/activity_repository.py) into text, in the
viewer's language: describe(event) -> (title, detail). Strings live under
`activity.<kind>.title` / `.detail` in the merged i18n tables, with
{placeholders} taken from the event's data plus {location}, {actor}."""

from __future__ import annotations

from datetime import datetime

from shared.currency import format_money
from shared.i18n import tr
from shared.models import ACTIVITY_SEVERITIES, ActivityEvent

_MONEY_FIELDS = ("total", "expected", "counted", "difference")


class _Safe(dict):
    def __missing__(self, key):
        return "—"


def _values(event: ActivityEvent) -> _Safe:
    values = _Safe(event.data)
    for field in _MONEY_FIELDS:
        if isinstance(values.get(field), (int, float)):
            values[field] = format_money(values[field])
    method = event.data.get("method")
    values["method"] = tr(f"pos.sale.{method}") if method in ("card", "cash") else tr("pos.sales.method_none")
    values["location"] = event.location_name or event.location_code or tr("activity.somewhere")
    values["actor"] = event.actor or tr("activity.someone")
    note = event.data.get("note")
    values["note"] = f" · {note}" if note else ""
    reference = event.data.get("reference")
    values["reference"] = reference or tr("activity.manual")
    values["direction"] = "+" if event.data.get("direction") == "receive" else "−"
    return values


def _text(key: str, values: _Safe) -> str:
    template = tr(key)
    if template == key:  # no translation for a future kind: say nothing rather than show a key
        return ""
    try:
        return template.format_map(values)
    except (KeyError, ValueError, IndexError):
        return template


def describe(event: ActivityEvent) -> tuple[str, str]:
    values = _values(event)
    title = _text(f"activity.{event.kind}.title", values) or event.kind.replace("_", " ")
    return title, _text(f"activity.{event.kind}.detail", values)


def severity_label(severity: str) -> str:
    return tr(f"activity.severity.{severity}") if severity in ACTIVITY_SEVERITIES else severity


def source_label(source: str) -> str:
    return tr(f"activity.source.{source}")


def ago_text(when: datetime, now: datetime | None = None) -> str:
    """"just now" / "5 min ago" / "3 h ago" / the date."""
    now = now or datetime.now(when.tzinfo)
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 45:
        return tr("activity.ago_now")
    if seconds < 3600:
        return tr("activity.ago_min").format(n=max(1, seconds // 60))
    if seconds < 86400:
        return tr("activity.ago_hour").format(n=seconds // 3600)
    return when.strftime("%d.%m %H:%M")
