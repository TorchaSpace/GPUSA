"""All Employee CRUD/queries. The only place SQL for `employees` is
allowed to live - mirrors dealership_repository.py's shape exactly (same
function names, same get-by-natural-key convention), with `badge_id`
playing the role `code` plays there.

Scope note: this is deliberately a minimal v1, same call as Dealerships.
The Workforce mockup (Workforce.dc.html) also shows a full weekly shift
schedule/calendar, a per-week attendance rate and absence count, and a
per-day present/absent/off grid - all entirely fabricated client-side
(a hardcoded 15-person roster stretched to a claimed "229 employees",
attendance driven by a sine-based pseudo-random function), not derived
from anything the mockup actually persists. See attendance_repository.py
for the real badge check-in/out this domain DOES get instead - a genuine
workflow the Warehouse Console mockup's own "Workforce Attendance" tab
shows, unlike Workforce.dc.html's fabricated dashboard.
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import DuplicateBadgeIdError, EmployeeNotFoundError
from shared.models import EMPLOYEE_LOCATION_TYPES, EMPLOYEE_ROLES, Employee


def _validate_role(role: str) -> None:
    if role not in EMPLOYEE_ROLES:
        raise ValueError(f"role must be one of {EMPLOYEE_ROLES!r}, got {role!r}")


def _validate_location_type(location_type: str) -> None:
    if location_type not in EMPLOYEE_LOCATION_TYPES:
        raise ValueError(
            f"location_type must be one of {EMPLOYEE_LOCATION_TYPES!r}, got {location_type!r}"
        )


def _guard_last_admin(conn, badge_id: str) -> None:
    """Switching off or deleting the only active administrator's employee
    record would lock everyone out of Admin - refuse (LastAdminError)."""
    from database.account_repository import guard_last_admin  # lazy: account_repository imports this layer's tables

    row = conn.execute("SELECT id FROM employees WHERE badge_id = ?", (badge_id,)).fetchone()
    if row is not None:
        guard_last_admin(conn, row["id"])


def _row_to_employee(row: sqlite3.Row) -> Employee:
    return Employee(
        id=row["id"],
        badge_id=row["badge_id"],
        name=row["name"],
        title=row["title"],
        role=row["role"],
        location_type=row["location_type"],
        location_name=row["location_name"],
        is_active=bool(row["is_active"]),
    )


def get_by_badge_id(badge_id: str) -> Employee:
    """Return the Employee for `badge_id`, or raise EmployeeNotFoundError."""
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE badge_id = ?", (badge_id,)
        ).fetchone()
    if row is None:
        raise EmployeeNotFoundError(badge_id)
    return _row_to_employee(row)


def list_all() -> list[Employee]:
    """Return every employee, e.g. for admin_app's Workforce page."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
    return [_row_to_employee(row) for row in rows]


def create(employee: Employee) -> None:
    """Insert a new employee. Raises DuplicateBadgeIdError if the badge id
    exists, ValueError if role/location_type aren't recognized."""
    _validate_role(employee.role)
    _validate_location_type(employee.location_type)
    with connection_scope() as conn:
        try:
            conn.execute(
                "INSERT INTO employees (badge_id, name, title, role, location_type, "
                "location_name, is_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    employee.badge_id,
                    employee.name,
                    employee.title,
                    employee.role,
                    employee.location_type,
                    employee.location_name,
                    int(employee.is_active),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateBadgeIdError(employee.badge_id) from exc


def update(employee: Employee) -> None:
    """Update an existing employee's fields, keyed by its (fixed) badge id.
    Raises ValueError if role/location_type aren't recognized."""
    _validate_role(employee.role)
    _validate_location_type(employee.location_type)
    with connection_scope() as conn:
        if not employee.is_active:
            _guard_last_admin(conn, employee.badge_id)
        cursor = conn.execute(
            "UPDATE employees SET name = ?, title = ?, role = ?, location_type = ?, "
            "location_name = ?, is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "WHERE badge_id = ?",
            (
                employee.name,
                employee.title,
                employee.role,
                employee.location_type,
                employee.location_name,
                int(employee.is_active),
                employee.badge_id,
            ),
        )
    if cursor.rowcount == 0:
        raise EmployeeNotFoundError(employee.badge_id)


def delete(badge_id: str) -> None:
    """Remove an employee. Raises EmployeeNotFoundError if it doesn't exist,
    or ValueError if the employee has attendance history (the `employees`
    row is a FK target of `attendance_records` - deactivate via update()
    instead of deleting a badge that's already clocked in/out at least once)."""
    with connection_scope() as conn:
        _guard_last_admin(conn, badge_id)
        try:
            cursor = conn.execute("DELETE FROM employees WHERE badge_id = ?", (badge_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Cannot delete {badge_id!r}: it has attendance history. Set it inactive instead."
            ) from exc
    if cursor.rowcount == 0:
        raise EmployeeNotFoundError(badge_id)
