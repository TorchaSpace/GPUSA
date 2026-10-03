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

from shared.i18n import UserError, enum_label, tr
from database.connection import connection_scope
from database.exceptions import DuplicateBadgeIdError, EmployeeNotFoundError, SelfActionError
from shared.auth import Actor, normalize_badge_id
from shared.models import EMPLOYEE_LOCATION_TYPES, EMPLOYEE_ROLES, Employee

MAX_BADGE_LENGTH = 32
MAX_NAME_LENGTH = 120
MAX_TITLE_LENGTH = 120
MAX_LOCATION_LENGTH = 120


def find_row(conn, badge_id: str) -> sqlite3.Row | None:
    """The employees row for `badge_id`, typed in any letter case with
    stray spaces. Badges are stored upper-case, but rows saved before that
    rule may be lower-case, so both are matched; an exact match wins if
    (legacy) twins exist. Every repository looks employees up through here."""
    raw = (badge_id or "").strip()
    return conn.execute(
        "SELECT * FROM employees WHERE badge_id = ? OR upper(badge_id) = ? "
        "ORDER BY (badge_id = ?) DESC, id LIMIT 1",
        (raw, raw.upper(), raw.upper()),
    ).fetchone()


def validate_fields(employee: Employee, *, require_badge: bool = True) -> None:
    """Raise ValueError (message fit to show) for an empty or over-long
    badge / name / title / location. Pure - no database."""
    badge = normalize_badge_id(employee.badge_id)
    if require_badge:
        if not badge:
            raise UserError("err.badge_required")
        if len(badge) > MAX_BADGE_LENGTH:
            raise UserError("err.badge_too_long", n=MAX_BADGE_LENGTH)
    if not (employee.name or "").strip():
        raise UserError("err.employee_name_required")
    if len(employee.name.strip()) > MAX_NAME_LENGTH:
        raise UserError("err.name_too_long", n=MAX_NAME_LENGTH)
    if len((employee.title or "").strip()) > MAX_TITLE_LENGTH:
        raise UserError("err.title_too_long", n=MAX_TITLE_LENGTH)
    if not (employee.location_name or "").strip():
        raise UserError("err.location_required")
    if len(employee.location_name.strip()) > MAX_LOCATION_LENGTH:
        raise UserError("err.location_too_long", n=MAX_LOCATION_LENGTH)


def location_problem(location_type: str, location_name: str, known: dict[str, list[str]]) -> str | None:
    """Why `location_name` isn't an existing place of `location_type`
    ("Warehouse" / "Dealership"), or None. `known` maps each type to the
    names (and codes) that exist - see location_choices(). Case-insensitive."""
    wanted = " ".join((location_name or "").split()).casefold()
    names = {" ".join(n.split()).casefold() for n in known.get(location_type, [])}
    if wanted in names:
        return None
    kind = enum_label("location_type", location_type).lower()
    return tr("err.location_unknown").format(kind=kind, name=repr(location_name.strip()))


def location_choices(include_codes: bool = False) -> dict[str, list[str]]:
    """{"Warehouse": [names...], "Dealership": [names...]} that exist now -
    the pick list for the employee form. With include_codes, each place's
    code is listed too (what check_location() accepts)."""
    with connection_scope() as conn:
        warehouses = conn.execute("SELECT name, code FROM warehouses ORDER BY name").fetchall()
        dealerships = conn.execute("SELECT name, code FROM dealerships ORDER BY name").fetchall()

    def names(rows):
        out = [r["name"] for r in rows]
        return out + [r["code"] for r in rows] if include_codes else out

    return {"Warehouse": names(warehouses), "Dealership": names(dealerships)}


def _validate_role(role: str) -> None:
    if role not in EMPLOYEE_ROLES:
        raise ValueError(f"role must be one of {EMPLOYEE_ROLES!r}, got {role!r}")


def _validate_location_type(location_type: str) -> None:
    if location_type not in EMPLOYEE_LOCATION_TYPES:
        raise ValueError(
            f"location_type must be one of {EMPLOYEE_LOCATION_TYPES!r}, got {location_type!r}"
        )


def _guard_row_last_admin(conn, employee_id: int) -> None:
    """Switching off or deleting the only active administrator's employee
    record would lock everyone out of Admin - refuse (LastAdminError)."""
    from database.account_repository import guard_last_admin  # lazy: account_repository imports this module

    guard_last_admin(conn, employee_id)


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
    """Return the Employee for `badge_id` (any letter case, stray spaces
    ignored), or raise EmployeeNotFoundError."""
    with connection_scope() as conn:
        row = find_row(conn, badge_id)
    if row is None:
        raise EmployeeNotFoundError(normalize_badge_id(badge_id))
    return _row_to_employee(row)


def list_all() -> list[Employee]:
    """Return every employee, e.g. for admin_app's Workforce page."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
    return [_row_to_employee(row) for row in rows]


def _clean(employee: Employee) -> None:
    """Normalise in place: badge upper-case and trimmed, text trimmed."""
    employee.badge_id = normalize_badge_id(employee.badge_id)
    employee.name = " ".join((employee.name or "").split())
    employee.title = " ".join((employee.title or "").split()) or None
    employee.location_name = " ".join((employee.location_name or "").split())


def _same_place(a: tuple[str, str], b: tuple[str, str]) -> bool:
    return a[0] == b[0] and a[1].casefold() == b[1].casefold()


def _check_location(employee: Employee) -> None:
    problem = location_problem(employee.location_type, employee.location_name, location_choices(include_codes=True))
    if problem:
        raise ValueError(problem)


def create(employee: Employee, *, check_location: bool = False) -> None:
    """Insert a new employee. The badge is stored trimmed and upper-case.
    Raises DuplicateBadgeIdError if the badge exists (in any letter case),
    ValueError if role/location_type aren't recognized or the badge, name or
    location is empty / too long. With `check_location=True` (the Workforce
    form) the location must also be an existing warehouse or dealership of
    that type; plain callers (setup scripts, tests) may use free text."""
    _validate_role(employee.role)
    _validate_location_type(employee.location_type)
    _clean(employee)
    validate_fields(employee)
    if check_location:
        _check_location(employee)
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if find_row(conn, employee.badge_id) is not None:
                raise DuplicateBadgeIdError(employee.badge_id)
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
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def update(employee: Employee, *, by: Actor | None = None, check_location: bool = False) -> None:
    """Update an existing employee's fields, keyed by its (fixed) badge id.
    Raises ValueError if role/location_type aren't recognized or a field is
    empty / too long, EmployeeNotFoundError, LastAdminError, and
    SelfActionError when `by` (the signed-in actor) switches themselves off.
    Switching an employee off also closes their open attendance shift
    (auto check-out at that moment). `check_location` as in create(); it is
    only enforced when the location actually changes, so an employee whose
    saved location is free text from before can still be edited."""
    from database import attendance_repository  # lazy: it imports this module

    _validate_role(employee.role)
    _validate_location_type(employee.location_type)
    _clean(employee)
    validate_fields(employee)
    known = location_choices(include_codes=True) if check_location else None
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = find_row(conn, employee.badge_id)
            if row is None:
                raise EmployeeNotFoundError(employee.badge_id)
            if known is not None and not _same_place(
                (row["location_type"], row["location_name"]), (employee.location_type, employee.location_name)
            ):
                problem = location_problem(employee.location_type, employee.location_name, known)
                if problem:
                    raise ValueError(problem)
            if not employee.is_active:
                if by is not None and normalize_badge_id(by.badge_id) == normalize_badge_id(row["badge_id"]):
                    raise SelfActionError("switch off")
                _guard_row_last_admin(conn, row["id"])
            conn.execute(
                "UPDATE employees SET name = ?, title = ?, role = ?, location_type = ?, "
                "location_name = ?, is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (
                    employee.name,
                    employee.title,
                    employee.role,
                    employee.location_type,
                    employee.location_name,
                    int(employee.is_active),
                    row["id"],
                ),
            )
            if row["is_active"] and not employee.is_active:
                attendance_repository.close_open_shifts(conn, row["id"], "Auto check-out: employee switched off")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def delete(badge_id: str, by: Actor | None = None) -> None:
    """Remove an employee - and, with them, their sign-in account (if any).
    Raises EmployeeNotFoundError if it doesn't exist, ValueError if the
    employee has attendance history (the `employees` row is a FK target of
    `attendance_records` - deactivate via update() instead), LastAdminError,
    and SelfActionError when `by` is the employee being removed."""
    with connection_scope() as conn:
        row = find_row(conn, badge_id)
        if row is None:
            raise EmployeeNotFoundError(normalize_badge_id(badge_id))
        if by is not None and normalize_badge_id(by.badge_id) == normalize_badge_id(row["badge_id"]):
            raise SelfActionError("remove")
        _guard_row_last_admin(conn, row["id"])
        try:
            conn.execute("DELETE FROM employees WHERE id = ?", (row["id"],))
        except sqlite3.IntegrityError as exc:
            raise UserError("err.employee_delete_history", badge=repr(row["badge_id"])) from exc
