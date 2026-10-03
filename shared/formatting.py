"""Display formatting shared by all three apps: database timestamps and
money amounts.

Every table stores timestamps as UTC ISO strings written by SQLite's
strftime('%Y-%m-%dT%H:%M:%fZ', 'now') - e.g. "2026-09-25T07:01:46.123Z".
These turn one into what a screen shows: a local clock time, a local
date+time, or a relative age ("14 min", "2 h") like the admin mockup's
approvals panel. Pure functions - no Qt, no database.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_db_timestamp(value: str) -> datetime:
    """Parse a database timestamp into an aware UTC datetime."""
    text = value.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp {value!r}")


def local_time_text(value: str | None) -> str:
    """"14:32" in the machine's local time zone, or "—"."""
    if not value:
        return "—"
    try:
        return parse_db_timestamp(value).astimezone().strftime("%H:%M")
    except ValueError:
        return "—"


def local_datetime_text(value: str | None) -> str:
    """"25.09.2026 14:32" in the machine's local time zone, or "—"."""
    if not value:
        return "—"
    try:
        return parse_db_timestamp(value).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return "—"


def age_text(value: str | None, now: datetime | None = None) -> str:
    """How long ago, compactly: "just now", "14 min", "5 h", "3 d"."""
    if not value:
        return "—"
    try:
        then = parse_db_timestamp(value)
    except ValueError:
        return "—"
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def format_amount(value: float | None) -> str:
    """Money for display: thousands separators, two decimals ("1,612.00").
    No currency symbol - the app shows plain amounts everywhere else
    (receipts, reports, product prices), so this doesn't invent one."""
    if value is None:
        return "—"
    return f"{value:,.2f}"


def parse_amount(text: str) -> float | None:
    """Read a typed money amount, accepting either decimal convention:
    "742.50", "742,50" (Turkish/European comma, as in the depot mockup's
    own form), "1,612.00", "1.612,00". Returns None for blank or
    unparseable input rather than raising - callers show their own hint.
    """
    cleaned = (text or "").strip().replace(" ", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")  # 1.612,00
        else:
            cleaned = cleaned.replace(",", "")  # 1,612.00
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_db_timestamp(value) -> str:
    """A datetime -> the database's UTC ISO format ("...T07:01:46.123Z").
    A naive datetime is taken to be local time (what a date/time picker
    on this machine gives you)."""
    from datetime import timezone as _tz

    if value.tzinfo is None:
        value = value.astimezone()  # attach the machine's local zone
    utc = value.astimezone(_tz.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def now_db_timestamp() -> str:
    from datetime import datetime as _dt, timezone as _tz

    return to_db_timestamp(_dt.now(_tz.utc))
