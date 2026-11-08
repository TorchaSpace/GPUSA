"""Pure staff / attendance logic shared by the depot, the dealership till
and Admin (no Qt, no SQL).

* StaffPlace - the one place a screen manages people for: a warehouse (the
  depot) or a dealership (the till). Staff added there are assigned to it,
  and only its own people can be checked in or out there.
* history_rows() - attendance records turned into the "who came, who left,
  which day, what time, how long" lines the history tables show.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from shared.formatting import parse_db_timestamp

WAREHOUSE = "Warehouse"
DEALERSHIP = "Dealership"


def _loose(text: str | None) -> str:
    """Case-, accent- and spacing-insensitive form (Turkish İ/ı included)."""
    decomposed = unicodedata.normalize("NFKD", (text or "").strip())
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(bare.casefold().replace("ı", "i").split())


@dataclass(frozen=True)
class StaffPlace:
    """A warehouse or dealership a screen works for. `kind` is the employees.location_type value."""

    kind: str  # WAREHOUSE | DEALERSHIP
    code: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}" if self.name else self.code

    def has(self, location_type: str | None, location_name: str | None) -> bool:
        """Whether an employee record (its location_type / free-text location_name) belongs here:
        same kind, and the name is this place's code, name or "code · name" label, loosely compared."""
        if location_type != self.kind:
            return False
        wanted = _loose(location_name)
        return bool(wanted) and wanted in {_loose(self.code), _loose(self.name), _loose(self.label)}


def _local(stamp: str) -> datetime:
    return parse_db_timestamp(stamp).astimezone()


def worked_minutes(check_in_at: str, check_out_at: str | None, now: datetime | None = None) -> int:
    """Whole minutes between the two stamps (to `now` while still open)."""
    end = parse_db_timestamp(check_out_at) if check_out_at else (now or datetime.now(timezone.utc))
    return int(max((end - parse_db_timestamp(check_in_at)).total_seconds(), 0) // 60)


def worked_hours(check_in_at: str, check_out_at: str | None, now: datetime | None = None) -> float:
    """Hours between the two stamps (to `now` while still open), rounded to 0.1."""
    return round(worked_minutes(check_in_at, check_out_at, now) / 60, 1)


def duration_text(minutes: int | None) -> str:
    """"8 h 05 min" / "45 min" (in the app's language); "—" for nothing."""
    from shared.i18n import tr

    if minutes is None:
        return "—"
    hours, mins = divmod(max(int(minutes), 0), 60)
    if hours:
        return tr("staff.duration.hm").format(h=hours, m=f"{mins:02d}")
    return tr("staff.duration.m").format(m=mins)


def clock_on(stamp: str | None, day: date | None = None) -> str:
    """"14:32" (local time) for a stamp on `day`; "09.11 07:10" when it falls on another day
    (a night shift's check-out); "—" for nothing."""
    if not stamp:
        return "—"
    moment = _local(stamp)
    if day is None or moment.date() == day:
        return moment.strftime("%H:%M")
    return moment.strftime("%d.%m %H:%M")


def date_text(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def weekday_text(day: date) -> str:
    from shared.i18n import tr

    return tr(f"staff.weekday.{day.weekday()}")


def history_rows(records: list[dict], people: dict[str, dict], now: datetime | None = None) -> list[dict]:
    """One line per attendance record, newest check-in first.

    `records`: dicts with badge_id, name, check_in_at, check_out_at (db stamps).
    `people`: badge_id -> employee dict (title, role, location_type, location_name) - who is listed
    is decided by the caller; a record whose badge is not in `people` is left out.

    Each line: day (local date), badge_id, name, title, role, check_in_at, check_out_at,
    minutes / hours (counted to now while open), open (bool)."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for record in records:
        person = people.get(record["badge_id"])
        if person is None:
            continue
        minutes = worked_minutes(record["check_in_at"], record.get("check_out_at"), now)
        rows.append({
            "day": _local(record["check_in_at"]).date(),
            "badge_id": record["badge_id"],
            "name": record.get("name") or person.get("name", ""),
            "title": person.get("title"),
            "role": person.get("role"),
            "location_type": person.get("location_type"),
            "location_name": person.get("location_name"),
            "check_in_at": record["check_in_at"],
            "check_out_at": record.get("check_out_at"),
            "minutes": minutes,
            "hours": round(minutes / 60, 1),
            "open": not record.get("check_out_at"),
        })
    rows.sort(key=lambda r: r["check_in_at"], reverse=True)
    return rows


def total_minutes(rows: list[dict]) -> int:
    return sum(r["minutes"] for r in rows)


def period(preset: str, today: date | None = None) -> tuple[date, date]:
    """(first day, last day) for the history's quick picks: today, yesterday, week (Monday to today),
    month (the 1st to today), last30."""
    today = today or date.today()
    if preset == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if preset == "week":
        return today - timedelta(days=today.weekday()), today
    if preset == "month":
        return today.replace(day=1), today
    if preset == "last30":
        return today - timedelta(days=29), today
    return today, today
