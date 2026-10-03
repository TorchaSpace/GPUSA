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
from datetime import datetime

from database.connection import connection_scope
from database.exceptions import (
    AlreadyCheckedInError,
    EmployeeNotFoundError,
    NoOpenAttendanceRecordError,
)


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")


def _hours_between(check_in_at: str, check_out_at: str) -> float:
    delta = _parse_ts(check_out_at) - _parse_ts(check_in_at)
    return round(delta.total_seconds() / 3600, 1)


def check_in(badge_id: str, note: str | None = None) -> int:
    """Open a new attendance record for `badge_id`. Returns the new
    record's id. Raises EmployeeNotFoundError for an unknown badge,
    AlreadyCheckedInError if that employee already has an open record
    (writing nothing) - a badge can't check in twice without checking
    out first.
    """
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            employee_row = conn.execute(
                "SELECT id FROM employees WHERE badge_id = ?", (badge_id,)
            ).fetchone()
            if employee_row is None:
                raise EmployeeNotFoundError(badge_id)
            employee_id = employee_row["id"]

            open_row = conn.execute(
                "SELECT id FROM attendance_records WHERE employee_id = ? AND check_out_at IS NULL",
                (employee_id,),
            ).fetchone()
            if open_row is not None:
                raise AlreadyCheckedInError(badge_id)

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
    overwrites whatever note check_in() set; left as-is otherwise.
    """
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            employee_row = conn.execute(
                "SELECT id FROM employees WHERE badge_id = ?", (badge_id,)
            ).fetchone()
            if employee_row is None:
                raise EmployeeNotFoundError(badge_id)
            employee_id = employee_row["id"]

            open_row = conn.execute(
                "SELECT id FROM attendance_records WHERE employee_id = ? AND check_out_at IS NULL "
                "ORDER BY check_in_at DESC LIMIT 1",
                (employee_id,),
            ).fetchone()
            if open_row is None:
                raise NoOpenAttendanceRecordError(badge_id)

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


def list_roster(for_date: str | None = None) -> list[dict]:
    """Every active-or-not employee, each paired with their latest
    attendance record for `for_date` (default: today), for depot
    Console's roster table and admin_app's Workforce page.

    Returns dicts with: badge_id, name, title, role, location_type,
    location_name, is_active, status ("Present" - checked in, no
    check-out yet; "Checked out" - a completed cycle that day; "Off" -
    no record that day), check_in_at, check_out_at, hours (float,
    rounded, or None until checked out).
    """
    query_date = for_date or _date.today().isoformat()
    query = """
        SELECT e.badge_id, e.name, e.title, e.role, e.location_type, e.location_name,
               e.is_active, a.check_in_at, a.check_out_at
        FROM employees e
        LEFT JOIN attendance_records a ON a.id = (
            SELECT a2.id FROM attendance_records a2
            WHERE a2.employee_id = e.id AND date(a2.check_in_at) = date(?)
            ORDER BY a2.check_in_at DESC LIMIT 1
        )
        ORDER BY e.name
    """
    with connection_scope() as conn:
        rows = conn.execute(query, (query_date,)).fetchall()

    roster = []
    for row in rows:
        check_in_at = row["check_in_at"]
        check_out_at = row["check_out_at"]
        if check_in_at is None:
            status = "Off"
            hours = None
        elif check_out_at is None:
            status = "Present"
            hours = None
        else:
            status = "Checked out"
            hours = _hours_between(check_in_at, check_out_at)
        roster.append(
            {
                "badge_id": row["badge_id"],
                "name": row["name"],
                "title": row["title"],
                "role": row["role"],
                "location_type": row["location_type"],
                "location_name": row["location_name"],
                "is_active": bool(row["is_active"]),
                "status": status,
                "check_in_at": check_in_at,
                "check_out_at": check_out_at,
                "hours": hours,
            }
        )
    return roster
