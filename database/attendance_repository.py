"""Badge check-in/check-out. The only place SQL for `attendance_records`
is allowed to live - depot_app Console's real equivalent of what
inventory_repository.py is for stock_movements: each check-in/check-out
is written atomically (BEGIN IMMEDIATE), and `check_out_at IS NULL` is
the single source of truth for "still on the floor" (see list_open()).

Scope note: this is the REAL half of the Employee/Workforce/Attendance
domain - the Warehouse Console mockup's own "Workforce Attendance" tab
(badge scan, on-floor count, checked-in/checked-out/hours columns) is a
genuine, buildable workflow, unlike admin_app's Workforce.dc.html, whose
weekly schedule calendar and attendance-rate/absence numbers are entirely
fabricated client-side (see employee_repository.py's module docstring).
admin_app's Workforce page reads this same real data instead of that
fabricated one.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, time, timedelta, timezone

from database import employee_repository
from database.connection import connection_scope
from database.exceptions import (
    AlreadyCheckedInError,
    EmployeeInactiveError,
    EmployeeNotFoundError,
    NoOpenAttendanceRecordError,
)
from shared.formatting import parse_db_timestamp, to_db_timestamp

# An open shift older than this is almost certainly a forgotten check-out:
# the roster still shows the person Present, but flags it (`long_open`).
LONG_SHIFT_HOURS = 16


def _hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def _employee(conn, badge_id: str):
    """The employees row for a typed/scanned badge (any case, stray spaces)."""
    row = employee_repository.find_row(conn, badge_id)
    if row is None:
        raise EmployeeNotFoundError(employee_repository.normalize_badge_id(badge_id))
    return row


def check_in(badge_id: str, note: str | None = None) -> int:
    """Open a new attendance record for `badge_id` (any letter case).
    Returns the new record's id. Raises EmployeeNotFoundError for an
    unknown badge, EmployeeInactiveError for an employee switched off in
    Workforce, AlreadyCheckedInError if that employee already has an open
    record (writing nothing) - a badge can't check in twice without
    checking out first.
    """
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            employee_row = _employee(conn, badge_id)
            if not employee_row["is_active"]:
                raise EmployeeInactiveError(employee_row["badge_id"])
            employee_id = employee_row["id"]

            open_row = conn.execute(
                "SELECT id FROM attendance_records WHERE employee_id = ? AND check_out_at IS NULL",
                (employee_id,),
            ).fetchone()
            if open_row is not None:
                raise AlreadyCheckedInError(employee_row["badge_id"])

            cursor = conn.execute(
                "INSERT INTO attendance_records (employee_id, note) VALUES (?, ?)",
                (employee_id, note),
            )
            new_id = cursor.lastrowid
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return new_id


def check_out(badge_id: str, note: str | None = None) -> int:
    """Close `badge_id`'s open attendance record. Returns the record's id.
    Raises EmployeeNotFoundError for an unknown badge, NoOpenAttendanceRecordError
    if that employee has no open record to close. `note`, when given,
    overwrites whatever note check_in() set; left as-is otherwise. (An
    inactive employee can still be checked out - that is how a shift left
    open at deactivation, in older data, gets closed.)
    """
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            employee_row = _employee(conn, badge_id)
            employee_id = employee_row["id"]

            open_row = conn.execute(
                "SELECT id FROM attendance_records WHERE employee_id = ? AND check_out_at IS NULL "
                "ORDER BY check_in_at DESC LIMIT 1",
                (employee_id,),
            ).fetchone()
            if open_row is None:
                raise NoOpenAttendanceRecordError(employee_row["badge_id"])

            conn.execute(
                "UPDATE attendance_records SET check_out_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                "note = COALESCE(?, note) WHERE id = ?",
                (note, open_row["id"]),
            )
            record_id = open_row["id"]
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return record_id


def close_open_shifts(conn, employee_id: int, note: str) -> int:
    """Check out every open record of `employee_id` right now, inside the
    caller's transaction (employee_repository.update() uses this when it
    switches someone off). The note is appended to any existing one.
    Returns how many were closed."""
    cursor = conn.execute(
        "UPDATE attendance_records SET check_out_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        "note = CASE WHEN note IS NULL OR note = '' THEN ? ELSE note || ' · ' || ? END "
        "WHERE employee_id = ? AND check_out_at IS NULL",
        (note, note, employee_id),
    )
    return cursor.rowcount


def list_open() -> list[dict]:
    """Every employee currently checked in (no check_out_at yet) - depot
    Console's "on floor" count and admin_app's "on shift now" KPI both
    just need len() of this."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT e.badge_id, e.name, e.role, a.check_in_at "
            "FROM attendance_records a JOIN employees e ON e.id = a.employee_id "
            "WHERE a.check_out_at IS NULL ORDER BY a.check_in_at"
        ).fetchall()
    return [dict(row) for row in rows]


def list_punches(for_date: str | None = None, limit: int = 12) -> list[dict]:
    """The check-in and check-out EVENTS of the LOCAL day `for_date` (default
    today), newest first, for the Floor's "Today's punches" list. Each is a
    dict: at (the stamp), name, badge_id, action ("IN" or "OUT")."""
    day = _date.fromisoformat(for_date) if for_date else _date.today()
    start, end = _day_window(day)
    low, high = to_db_timestamp(start), to_db_timestamp(end)
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT e.badge_id, e.name, a.check_in_at, a.check_out_at "
            "FROM attendance_records a JOIN employees e ON e.id = a.employee_id "
            "WHERE (a.check_in_at >= ? AND a.check_in_at < ?) OR (a.check_out_at >= ? AND a.check_out_at < ?)",
            (low, high, low, high),
        ).fetchall()
    events = []
    for row in rows:
        if low <= row["check_in_at"] < high:
            events.append({"at": row["check_in_at"], "name": row["name"], "badge_id": row["badge_id"], "action": "IN"})
        if row["check_out_at"] and low <= row["check_out_at"] < high:
            events.append({"at": row["check_out_at"], "name": row["name"], "badge_id": row["badge_id"], "action": "OUT"})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[: max(0, limit)]


def _day_window(day: _date) -> tuple[datetime, datetime]:
    """[start, end) of the LOCAL calendar day `day`, as aware UTC datetimes."""
    start = datetime.combine(day, time.min).astimezone()  # a naive datetime is read as local time
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def list_roster(for_date: str | None = None, now: datetime | None = None) -> list[dict]:
    """Every active-or-not employee with their attendance on the LOCAL day
    `for_date` (default: today), for depot Console's roster table, the
    Overview's employee log (shared.overview.attendance_today) and
    admin_app's Workforce page.

    Returns dicts with: badge_id, name, title, role, location_type,
    location_name, is_active, status, check_in_at, check_out_at, hours,
    long_open.
    - status "Present": the employee has an open record (no check-out yet)
      that began before the end of that day - whatever day it began on, so
      a shift opened yesterday and never closed still shows Present.
      check_in_at is that record's start; `long_open` is True when it has
      been open more than LONG_SHIFT_HOURS (probably a forgotten
      check-out).
    - "Checked out": every record touching the day is closed. check_in_at /
      check_out_at are those of the latest one.
    - "Off": no record touches the day.
    - hours: the day's COMPLETED records added up (a night shift counts only
      the part inside the day), rounded to 0.1; None if there are none.
    """
    day = _date.fromisoformat(for_date) if for_date else _date.today()
    start, end = _day_window(day)
    now = now or datetime.now(timezone.utc)
    with connection_scope() as conn:
        employees = conn.execute(
            "SELECT id, badge_id, name, title, role, location_type, location_name, is_active "
            "FROM employees ORDER BY name"
        ).fetchall()
        records = conn.execute(
            "SELECT employee_id, check_in_at, check_out_at FROM attendance_records "
            "WHERE check_in_at < ? AND (check_out_at IS NULL OR check_out_at > ?) ORDER BY check_in_at",
            (to_db_timestamp(end), to_db_timestamp(start)),
        ).fetchall()

    by_employee: dict[int, list] = {}
    for record in records:
        by_employee.setdefault(record["employee_id"], []).append(record)

    roster = []
    for emp in employees:
        mine = by_employee.get(emp["id"], [])
        opened = [r for r in mine if r["check_out_at"] is None]
        closed = [r for r in mine if r["check_out_at"] is not None]
        hours = None
        if closed:
            worked = timedelta()
            for r in closed:
                begin = max(parse_db_timestamp(r["check_in_at"]), start)
                finish = min(parse_db_timestamp(r["check_out_at"]), end)
                worked += max(finish - begin, timedelta())
            hours = _hours(worked)
        long_open = False
        if opened:
            shown = opened[-1]
            status, check_in_at, check_out_at = "Present", shown["check_in_at"], None
            long_open = now - parse_db_timestamp(shown["check_in_at"]) > timedelta(hours=LONG_SHIFT_HOURS)
        elif closed:
            shown = closed[-1]
            status, check_in_at, check_out_at = "Checked out", shown["check_in_at"], shown["check_out_at"]
        else:
            status, check_in_at, check_out_at = "Off", None, None
        roster.append(
            {
                "badge_id": emp["badge_id"],
                "name": emp["name"],
                "title": emp["title"],
                "role": emp["role"],
                "location_type": emp["location_type"],
                "location_name": emp["location_name"],
                "is_active": bool(emp["is_active"]),
                "status": status,
                "check_in_at": check_in_at,
                "check_out_at": check_out_at,
                "hours": hours,
                "long_open": long_open,
            }
        )
    return roster
