"""Employees, attendance (badge check-in / check-out) and sign-in accounts.

Same rules as the desktop's employee_repository.py, attendance_repository.py and
account_repository.py:

- Badges are stored trimmed and upper-case and matched in any letter case.
- Switching an employee off closes their open shift; an employee with attendance history
  cannot be deleted (switch them off instead); deleting one also deletes their account.
- The last active administrator can never be demoted, switched off or deleted, and nobody
  can switch off, delete, demote or reset-without-PIN their own account.
- PINs: digits only, 4 to 12 long (6 to 12 for an administrator), no runs like 1111 or 1234.
  Promoting someone to administrator must come with a new, different PIN.
- Every account change lands in ``auth_events``; deletes also in ``admin_audit``.

Who: the depot (depot manager, depot console) and the dealership till (cashier, POS) add
and change their OWN place's staff and punch their badges in and out; administrators may do
it anywhere (they can sign in at a depot or a till) and alone manage accounts and deletes.
In the Admin app they only watch. Lists are scoped the same way: a depot manager sees their
warehouse's people, a cashier their shop's. Anyone may change their own PIN with the current one.

National IDs (T.C. Kimlik No, 11 digits with check digits) are stored whole but only ever sent
back masked ("123******90").
"""

from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import authz, db, security
from .auth_routes import LOCK_MINUTES, MAX_FAILED_ATTEMPTS, Session, _log as _auth_log, current_session
from .stock_routes import _clean, _fail, _write

router = APIRouter(tags=["people"])

ADMINS = frozenset({"admin"})
ADMIN_AREA = frozenset({"admin"})
PUNCH_ROLES = frozenset({"admin", "depot_manager", "cashier"})
PUNCH_AREAS = frozenset({"admin", "depot_console", "pos"})
STAFF_ROLES = frozenset({"admin", "depot_manager", "cashier"})
STAFF_AREAS = frozenset({"depot_console", "pos"})
EVERY_ROLE = frozenset({"admin", "depot_manager", "cashier"})
EVERY_AREA = frozenset({"admin", "depot_console", "pos"})

EMPLOYEE_ROLES = ("Operations", "Logistics", "Sales & service", "Management")
LOCATION_TYPES = ("Warehouse", "Dealership")
ACCOUNT_ROLES = ("admin", "depot_manager", "cashier")
MAX_BADGE, MAX_NAME, MAX_TITLE, MAX_LOCATION = 32, 120, 120, 120
MIN_PIN, MIN_ADMIN_PIN, MAX_PIN = 6, 6, 12


def _badge(text: str | None) -> str:
    return (text or "").strip().upper()


def _squash(text: str | None) -> str:
    return " ".join((text or "").split())


def _loose(text: str | None) -> str:
    """Case-, accent- and spacing-insensitive form for matching typed place names."""
    folded = unicodedata.normalize("NFKD", _squash(text).casefold().replace("ı", "i"))
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def pin_problem(pin: str, role: str) -> str | None:
    pin = pin or ""
    minimum = MIN_ADMIN_PIN if role == "admin" else MIN_PIN
    if not pin.isdigit() or not pin.isascii():
        return "A PIN is digits only."
    if len(pin) < minimum:
        return f"A {role.replace('_', ' ')} PIN needs at least {minimum} digits."
    if len(pin) > MAX_PIN:
        return f"A PIN can have at most {MAX_PIN} digits."
    if len(set(pin)) == 1 or pin in "0123456789012" or pin in "9876543210987":
        return "That PIN is too easy to guess (a repeated digit or a run like 1234)."
    return None


def national_id_problem(value: str) -> str | None:
    """Why `value` (digits only) isn't a valid T.C. Kimlik No, or None. Same rule as shared/national_id.py."""
    if not value.isascii() or not value.isdigit() or len(value) != 11:
        return "A national ID has 11 digits."
    if value[0] == "0":
        return "That national ID is not valid; check the digits."
    d = [int(ch) for ch in value]
    if d[9] != (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 or d[10] != sum(d[:10]) % 10:
        return "That national ID is not valid; check the digits."
    return None


def _national_id(text: str | None) -> str | None:
    value = "".join(ch for ch in (text or "") if not ch.isspace() and ch not in "-.")
    if not value:
        return None
    if problem := national_id_problem(value):
        raise _fail(422, problem)
    return value


MASKED_NATIONAL_ID = ("CASE WHEN national_id IS NULL THEN NULL ELSE left(national_id, 3) || "
                      "repeat('*', greatest(length(national_id) - 5, 0)) || right(national_id, 2) END "
                      "AS national_id_masked")


# --- whose staff ------------------------------------------------------------------------

def own_place(conn, session: Session) -> tuple[str, str, str] | None:
    """(location_type, code, name) of the one place this person manages staff for: a depot manager's
    warehouse, a cashier's dealership. None for an administrator (every place)."""
    if (code := authz.own_warehouse(conn, session)) is not None:
        row = conn.execute("SELECT code, name FROM warehouses WHERE upper(code) = upper(%s)", (code,)).fetchone()
        return ("Warehouse", row["code"], row["name"]) if row else ("Warehouse", code, "")
    if (code := authz.own_shop(conn, session)) is not None:
        row = conn.execute("SELECT code, name FROM dealerships WHERE upper(code) = upper(%s)", (code,)).fetchone()
        return ("Dealership", row["code"], row["name"]) if row else ("Dealership", code, "")
    return None


def at_place(location_type: str | None, location_name: str | None, place: tuple[str, str, str] | None) -> bool:
    if place is None:
        return True
    kind, code, name = place
    wanted = _loose(location_name)
    return location_type == kind and bool(wanted) and wanted in {_loose(code), _loose(name), _loose(f"{code} · {name}")}


def _require_own_place(conn, session: Session, location_type: str, location_name: str, badge: str) -> None:
    place = own_place(conn, session)
    if not at_place(location_type, location_name, place):
        raise _fail(403, f"{badge or location_name} is not one of {place[1]}'s staff; "
                         "a depot or a shop manages only its own people.")


# --- employees -------------------------------------------------------------------------

class EmployeeIn(BaseModel):
    badge_id: str = Field(max_length=64)
    name: str = Field(max_length=300)
    title: str | None = Field(default=None, max_length=300)
    role: str = Field(max_length=40)
    location_type: str = Field(max_length=20)
    location_name: str = Field(max_length=300)
    is_active: bool = True
    national_id: str | None = Field(default=None, max_length=40)


class EmployeeUpdate(BaseModel):
    name: str = Field(max_length=300)
    title: str | None = Field(default=None, max_length=300)
    role: str = Field(max_length=40)
    location_type: str = Field(max_length=20)
    location_name: str = Field(max_length=300)
    is_active: bool = True
    # None / left out = keep the saved one.
    national_id: str | None = Field(default=None, max_length=40)


class EmployeeOut(BaseModel):
    id: int
    badge_id: str
    name: str
    title: str | None
    role: str = Field(max_length=40)
    location_type: str = Field(max_length=20)
    location_name: str
    is_active: bool
    national_id_masked: str | None = None


_EMPLOYEE_COLUMNS = "id, badge_id, name, title, role, location_type, location_name, is_active, " + MASKED_NATIONAL_ID


def _guard_national_id(conn, national_id: str | None, own_id: int | None = None) -> None:
    if not national_id:
        return
    other = conn.execute("SELECT id, badge_id FROM employees WHERE national_id = %s", (national_id,)).fetchone()
    if other is not None and other["id"] != own_id:
        raise _fail(409, f"This national ID is already recorded for {other['badge_id']}.")


def _has_admin_account(conn, employee_id: int) -> bool:
    return conn.execute("SELECT 1 FROM accounts WHERE employee_id = %s AND role = 'admin'",
                        (employee_id,)).fetchone() is not None


def _validate_employee(body: EmployeeIn | EmployeeUpdate, *, badge: str | None = None) -> dict:
    if body.role not in EMPLOYEE_ROLES:
        raise _fail(422, f"role must be one of {EMPLOYEE_ROLES}.")
    if body.location_type not in LOCATION_TYPES:
        raise _fail(422, f"location_type must be one of {LOCATION_TYPES}.")
    out = {"name": _squash(body.name), "title": _squash(body.title) or None,
           "location_name": _squash(body.location_name)}
    if badge is not None:
        if not badge:
            raise _fail(422, "A badge ID is required.")
        if len(badge) > MAX_BADGE:
            raise _fail(422, f"A badge ID can have at most {MAX_BADGE} characters.")
    if not out["name"]:
        raise _fail(422, "A name is required.")
    if len(out["name"]) > MAX_NAME:
        raise _fail(422, f"A name can have at most {MAX_NAME} characters.")
    if out["title"] and len(out["title"]) > MAX_TITLE:
        raise _fail(422, f"A title can have at most {MAX_TITLE} characters.")
    if not out["location_name"]:
        raise _fail(422, "A location is required.")
    if len(out["location_name"]) > MAX_LOCATION:
        raise _fail(422, f"A location can have at most {MAX_LOCATION} characters.")
    return out


def _places(conn) -> dict[str, list[dict]]:
    return {"Warehouse": conn.execute("SELECT name, code FROM warehouses ORDER BY name").fetchall(),
            "Dealership": conn.execute("SELECT name, code FROM dealerships ORDER BY name").fetchall()}


def _location_problem(conn, location_type: str, location_name: str) -> str | None:
    wanted = _loose(location_name)
    known = {_loose(v) for place in _places(conn)[location_type] for v in (place["name"], place["code"])}
    if wanted in known:
        return None
    return f"There is no {location_type.lower()} called {location_name!r}."


def _find_employee(conn, badge: str, *, lock: bool = False):
    return conn.execute(
        "SELECT * FROM employees WHERE upper(badge_id) = %s ORDER BY (badge_id = %s) DESC, id LIMIT 1"
        + (" FOR UPDATE" if lock else ""), (_badge(badge), _badge(badge))).fetchone()


def _need_employee(conn, badge: str, *, lock: bool = False):
    row = _find_employee(conn, badge, lock=lock)
    if row is None:
        raise _fail(404, f"No employee with badge {_badge(badge)}.")
    return row


def _guard_last_admin(conn, employee_id: int) -> None:
    rows = conn.execute("SELECT a.employee_id FROM accounts a JOIN employees e ON e.id = a.employee_id "
                        "WHERE a.role = 'admin' AND a.is_active AND e.is_active").fetchall()
    if {r["employee_id"] for r in rows} == {employee_id}:
        raise _fail(409, "That is the only active administrator; someone else must be one first.")


def _audit(conn, session: Session, action: str, kind: str, target: str, detail: str | None) -> None:
    conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) VALUES (%s, %s, %s, %s, %s)",
                 (authz.actor_label(session), action, kind, target, detail))


@router.get("/employees/locations")
def employee_locations(request: Request, _session: Session = Depends(current_session)) -> dict:
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _places(conn)


@router.get("/employees/{badge}", response_model=EmployeeOut)
def get_employee(badge: str, request: Request, session: Session = Depends(current_session)) -> dict:
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        row = _need_employee(conn, badge)
        if session.role != "admin" and _badge(row["badge_id"]) != _badge(session.badge_id) and not at_place(
                row["location_type"], row["location_name"], own_place(conn, session)):
            raise _fail(404, f"No employee with badge {_badge(badge)}.")
        return conn.execute(f"SELECT {_EMPLOYEE_COLUMNS} FROM employees WHERE id = %s", (row["id"],)).fetchone()


@router.post("/employees", response_model=EmployeeOut, status_code=201)
def create_employee(body: EmployeeIn, request: Request, session: Session = Depends(current_session)) -> dict:
    """The depot or the till adds one of its own people (an administrator signed in there may add anyone)."""
    authz.require(session, roles=STAFF_ROLES, areas=STAFF_AREAS | ADMIN_AREA)
    if session.role != "admin" and session.area == "admin":
        raise _fail(403, "Sign in to depot_console or pos to do this.")
    badge = _badge(body.badge_id)
    fields = _validate_employee(body, badge=badge)
    national_id = _national_id(body.national_id)

    def work(conn):
        _require_own_place(conn, session, body.location_type, fields["location_name"], badge)
        if session.role != "admin" and national_id is None:
            raise _fail(422, "Enter the person's national ID (11 digits).")
        if _find_employee(conn, badge) is not None:
            raise _fail(409, f"An employee with badge {badge} exists already.")
        _guard_national_id(conn, national_id)
        if problem := _location_problem(conn, body.location_type, fields["location_name"]):
            raise _fail(422, problem)
        try:
            return conn.execute(
                "INSERT INTO employees (badge_id, name, title, role, location_type, location_name, is_active, "
                f"national_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_EMPLOYEE_COLUMNS}",
                (badge, fields["name"], fields["title"], body.role, body.location_type, fields["location_name"],
                 body.is_active, national_id)).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            if "national_id" in str(exc):
                raise _fail(409, "This national ID is already recorded for another employee.") from None
            raise _fail(409, f"An employee with badge {badge} exists already.") from None

    return _write(request, session, STAFF_ROLES, work)


@router.patch("/employees/{badge}", response_model=EmployeeOut)
def update_employee(badge: str, body: EmployeeUpdate, request: Request,
                    session: Session = Depends(current_session)) -> dict:
    """The depot or the till changes one of its own people (they stay its own: a depot manager or a cashier
    cannot move someone to another place, nor touch an administrator's record)."""
    authz.require(session, roles=STAFF_ROLES, areas=STAFF_AREAS | ADMIN_AREA)
    if session.role != "admin" and session.area == "admin":
        raise _fail(403, "Sign in to depot_console or pos to do this.")
    fields = _validate_employee(body)
    national_id = _national_id(body.national_id)

    def work(conn):
        row = _need_employee(conn, badge, lock=True)
        if session.role != "admin":
            _require_own_place(conn, session, row["location_type"], row["location_name"], row["badge_id"])
            _require_own_place(conn, session, body.location_type, fields["location_name"], row["badge_id"])
            if _has_admin_account(conn, row["id"]):
                raise _fail(403, f"{row['badge_id']} is an administrator; only an administrator can change them.")
        _guard_national_id(conn, national_id, row["id"])
        moved = (row["location_type"], _squash(row["location_name"]).casefold()) != (
            body.location_type, fields["location_name"].casefold())
        if moved and (problem := _location_problem(conn, body.location_type, fields["location_name"])):
            raise _fail(422, problem)
        if body.location_type != "Warehouse" and conn.execute(
                "SELECT 1 FROM accounts WHERE employee_id = %s AND role = 'depot_manager'", (row["id"],)).fetchone():
            raise _fail(422, f"{row['badge_id']} is a depot manager; they must stay at a warehouse "
                             "(change their role first).")
        if not body.is_active and row["is_active"]:
            if _badge(row["badge_id"]) == _badge(session.badge_id):
                raise _fail(409, "You cannot switch off your own record.")
            _guard_last_admin(conn, row["id"])
        updated = conn.execute(
            "UPDATE employees SET name = %s, title = %s, role = %s, location_type = %s, location_name = %s, "
            "is_active = %s, national_id = COALESCE(%s, national_id), updated_at = now() WHERE id = %s "
            f"RETURNING {_EMPLOYEE_COLUMNS}",
            (fields["name"], fields["title"], body.role, body.location_type, fields["location_name"],
             body.is_active, national_id, row["id"])).fetchone()
        if row["is_active"] and not body.is_active:
            _close_shifts(conn, row["id"], "Auto check-out: employee switched off")
        return updated

    return _write(request, session, STAFF_ROLES, work)


@router.delete("/employees/{badge}", status_code=204)
def delete_employee(badge: str, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _need_employee(conn, badge, lock=True)
        if _badge(row["badge_id"]) == _badge(session.badge_id):
            raise _fail(409, "You cannot remove your own record.")
        _guard_last_admin(conn, row["id"])
        if conn.execute("SELECT 1 FROM attendance_records WHERE employee_id = %s LIMIT 1", (row["id"],)).fetchone():
            raise _fail(409, f"{row['badge_id']} has attendance history and cannot be deleted; switch them off instead.")
        had_account = conn.execute("SELECT 1 FROM accounts WHERE employee_id = %s", (row["id"],)).fetchone()
        conn.execute("DELETE FROM employees WHERE id = %s", (row["id"],))
        _audit(conn, session, "deleted", "employee", row["badge_id"],
               row["name"] + (" · sign-in account removed" if had_account else ""))

    _write(request, session, ADMINS, work)


# --- attendance ------------------------------------------------------------------------

class PunchIn(BaseModel):
    badge_id: str = Field(max_length=64)
    note: str | None = Field(default=None, max_length=300)
    warehouse_code: str | None = Field(default=None, max_length=32)
    dealership_code: str | None = Field(default=None, max_length=32)


def _close_shifts(conn, employee_id: int, note: str) -> int:
    return conn.execute(
        "UPDATE attendance_records SET check_out_at = now(), "
        "note = CASE WHEN note IS NULL OR note = '' THEN %s ELSE note || ' · ' || %s END "
        "WHERE employee_id = %s AND check_out_at IS NULL", (note, note, employee_id)).rowcount


def _depot_manager_needs_warehouse(employee) -> None:
    if employee["location_type"] != "Warehouse":
        raise _fail(422, f"{employee['badge_id']} must work at a warehouse (set it under Employees) "
                         "before becoming a depot manager.")


def _require_site(conn, employee, warehouse_code: str | None) -> None:
    """With a warehouse code (the depot Floor) the employee must work at that warehouse."""
    if not warehouse_code:
        return
    site = conn.execute("SELECT code, name FROM warehouses WHERE upper(code) = %s", (warehouse_code.strip().upper(),)).fetchone()
    code, name = (site["code"], site["name"]) if site else (warehouse_code, "")
    wanted = _loose(employee["location_name"])
    allowed = {_loose(code), _loose(name), _loose(f"{code} · {name}")}
    if employee["location_type"] != "Warehouse" or not wanted or wanted not in allowed:
        raise _fail(409, f"{employee['badge_id']} works at {employee['location_name']}, not at this depot.")


def _require_shop(conn, employee, dealership_code: str | None) -> None:
    """With a dealership code (the till) the employee must work at that dealership."""
    if not dealership_code:
        return
    shop = conn.execute("SELECT code, name FROM dealerships WHERE upper(code) = %s",
                        (dealership_code.strip().upper(),)).fetchone()
    place = ("Dealership", shop["code"], shop["name"]) if shop else ("Dealership", dealership_code, "")
    if not at_place(employee["location_type"], employee["location_name"], place):
        raise _fail(409, f"{employee['badge_id']} works at {employee['location_name']}, not at this shop.")


def _punch_employee(conn, body: PunchIn, session: Session):
    employee = _need_employee(conn, body.badge_id, lock=True)
    # A depot manager punches only their own warehouse's people, a cashier only their own shop's.
    if session.role != "admin" and not at_place(employee["location_type"], employee["location_name"],
                                                own_place(conn, session)):
        raise _fail(409, f"{employee['badge_id']} works at {employee['location_name']}, not here.")
    _require_site(conn, employee, body.warehouse_code)
    _require_shop(conn, employee, body.dealership_code)
    return employee


@router.post("/attendance/check-in")
def check_in(body: PunchIn, request: Request, session: Session = Depends(current_session)) -> dict:
    authz.require(session, roles=PUNCH_ROLES, areas=PUNCH_AREAS)

    def work(conn):
        employee = _punch_employee(conn, body, session)
        if not employee["is_active"]:
            raise _fail(409, f"{employee['badge_id']} is switched off.")
        if conn.execute("SELECT 1 FROM attendance_records WHERE employee_id = %s AND check_out_at IS NULL",
                        (employee["id"],)).fetchone():
            raise _fail(409, f"{employee['badge_id']} is already checked in.")
        row = conn.execute("INSERT INTO attendance_records (employee_id, note) VALUES (%s, %s) RETURNING id",
                           (employee["id"], _clean(body.note))).fetchone()
        return {"id": row["id"]}

    return _write(request, session, PUNCH_ROLES, work)


@router.post("/attendance/check-out")
def check_out(body: PunchIn, request: Request, session: Session = Depends(current_session)) -> dict:
    authz.require(session, roles=PUNCH_ROLES, areas=PUNCH_AREAS)

    def work(conn):
        employee = _punch_employee(conn, body, session)
        open_row = conn.execute(
            "SELECT id FROM attendance_records WHERE employee_id = %s AND check_out_at IS NULL "
            "ORDER BY check_in_at DESC LIMIT 1", (employee["id"],)).fetchone()
        if open_row is None:
            raise _fail(409, f"{employee['badge_id']} is not checked in.")
        conn.execute("UPDATE attendance_records SET check_out_at = now(), note = COALESCE(%s, note) WHERE id = %s",
                     (_clean(body.note), open_row["id"]))
        return {"id": open_row["id"]}

    return _write(request, session, PUNCH_ROLES, work)


def _floor_operator(conn, badge_id: str, warehouse_code: str | None):
    """The employee a Floor movement is credited to: active, at this depot, and checked in right now."""
    employee = _need_employee(conn, badge_id)
    if not employee["is_active"]:
        raise _fail(409, f"{employee['badge_id']} is switched off.")
    _require_site(conn, employee, warehouse_code)
    if conn.execute("SELECT 1 FROM attendance_records WHERE employee_id = %s AND check_out_at IS NULL",
                    (employee["id"],)).fetchone() is None:
        raise _fail(409, f"{employee['badge_id']} is not checked in.")
    return employee


def operator_label(conn, badge_id: str, warehouse_code: str | None) -> str:
    """handled_by text ("Name · BADGE") for a movement typed in under another person's badge. Checked again
    here, inside the movement's own transaction, because the Floor's earlier check may be minutes old. Any
    problem is a 422 so it is never mistaken for the movement's own 404 / 409 answers."""
    try:
        employee = _floor_operator(conn, badge_id, warehouse_code)
    except HTTPException as exc:
        raise _fail(422, f"Operator badge: {exc.detail}") from None
    return f"{employee['name']} · {employee['badge_id']}"


@router.get("/attendance/operator")
def operator(badge_id: str, request: Request, warehouse_code: str | None = None,
             session: Session = Depends(current_session)) -> dict:
    """Who a Floor movement is credited to: active, at this depot, and checked in right now."""
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        employee = _floor_operator(conn, badge_id, warehouse_code)
        return {"badge_id": employee["badge_id"], "name": employee["name"]}


def _visible(conn, session: Session, rows: list[dict]) -> list[dict]:
    """Rows (with location_type / location_name) a depot manager or a cashier may see: their own place's.
    The location columns are dropped from what is returned."""
    place = None if session.role == "admin" else own_place(conn, session)
    return [{k: v for k, v in r.items() if k not in ("location_type", "location_name")}
            for r in rows if at_place(r["location_type"], r["location_name"], place)]


@router.get("/attendance/open")
def open_shifts(request: Request, session: Session = Depends(current_session)) -> list[dict]:
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _visible(conn, session, conn.execute(
            "SELECT e.badge_id, e.name, e.role, a.check_in_at, e.location_type, e.location_name "
            "FROM attendance_records a JOIN employees e ON e.id = a.employee_id WHERE a.check_out_at IS NULL "
            "ORDER BY a.check_in_at").fetchall())


@router.get("/attendance/records")
def records(start: datetime, end: datetime, request: Request,
            session: Session = Depends(current_session)) -> list[dict]:
    """Records touching [start, end): started before `end` and not finished before `start`.
    A depot manager gets their warehouse's people only, a cashier their shop's."""
    if end - start > timedelta(days=400):
        raise _fail(422, "Ask for at most about a year at a time.")
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _visible(conn, session, conn.execute(
            "SELECT a.id, e.badge_id, e.name, a.employee_id, a.check_in_at, a.check_out_at, "
            "e.location_type, e.location_name "
            "FROM attendance_records a JOIN employees e ON e.id = a.employee_id "
            "WHERE a.check_in_at < %s AND (a.check_out_at IS NULL OR a.check_out_at > %s) "
            "ORDER BY a.check_in_at, a.id", (end, start)).fetchall())


# --- accounts --------------------------------------------------------------------------

class AccountOut(BaseModel):
    id: int
    badge_id: str
    name: str
    role: str
    is_active: bool
    employee_active: bool
    location_type: str
    location_name: str
    failed_attempts: int
    locked_until: datetime | None
    last_sign_in_at: datetime | None


_ACCOUNT_SELECT = (
    "SELECT a.id, a.role, a.is_active, a.failed_attempts, a.locked_until, a.last_sign_in_at, a.employee_id, "
    "e.badge_id, e.name, e.is_active AS employee_active, e.location_type, e.location_name "
    "FROM accounts a JOIN employees e ON e.id = a.employee_id")


def _need_account(conn, badge: str, *, lock: bool = False):
    row = conn.execute(_ACCOUNT_SELECT + " WHERE upper(e.badge_id) = %s"
                       + (" FOR UPDATE OF a" if lock else ""), (_badge(badge),)).fetchone()
    if row is None:
        raise _fail(404, f"No account for badge {_badge(badge)}.")
    return row


def _same_person(session: Session, row) -> bool:
    return _badge(session.badge_id) == _badge(row["badge_id"])


def _event(conn, session: Session, event: str, badge: str, detail: str | None = None) -> None:
    _auth_log(conn, event, badge, session.area, "", (detail + " · " if detail else "") + f"by {session.name}")


class AccountIn(BaseModel):
    badge_id: str = Field(max_length=64)
    role: str = Field(max_length=40)
    pin: str = Field(max_length=64)


class PinIn(BaseModel):
    pin: str = Field(max_length=64)


class RoleIn(BaseModel):
    role: str = Field(max_length=40)
    new_pin: str | None = Field(default=None, max_length=64)


class ActiveIn(BaseModel):
    active: bool


class OwnPinIn(BaseModel):
    current_pin: str = Field(max_length=64)
    new_pin: str = Field(max_length=64)


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(request: Request, session: Session = Depends(current_session)) -> list[dict]:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(_ACCOUNT_SELECT + " ORDER BY e.name").fetchall()


@router.get("/accounts/without")
def employees_without_account(request: Request, session: Session = Depends(current_session)) -> list[dict]:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT badge_id, name FROM employees e WHERE is_active "
            "AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.employee_id = e.id) ORDER BY name").fetchall()


@router.get("/accounts/events")
def account_events(request: Request, limit: int = 100, session: Session = Depends(current_session)) -> list[dict]:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT v.created_at, v.badge_id, e.name, v.event, v.area, v.terminal, v.detail "
            "FROM auth_events v LEFT JOIN employees e ON e.badge_id = v.badge_id "
            "ORDER BY v.created_at DESC, v.id DESC LIMIT %s", (max(1, min(int(limit), 1000)),)).fetchall()


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountIn, request: Request, session: Session = Depends(current_session)) -> dict:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    if body.role not in ACCOUNT_ROLES:
        raise _fail(422, f"role must be one of {ACCOUNT_ROLES}.")
    if problem := pin_problem(body.pin, body.role):
        raise _fail(422, problem)
    pin_hash = security.hash_pin(body.pin)

    def work(conn):
        employee = _need_employee(conn, body.badge_id, lock=True)
        if not employee["is_active"]:
            raise _fail(409, f"{employee['badge_id']} is switched off; switch them on first.")
        if conn.execute("SELECT 1 FROM accounts WHERE employee_id = %s", (employee["id"],)).fetchone():
            raise _fail(409, f"{employee['badge_id']} has an account already.")
        if body.role == "depot_manager":
            _depot_manager_needs_warehouse(employee)
        conn.execute("INSERT INTO accounts (employee_id, role, pin_hash) VALUES (%s, %s, %s)",
                     (employee["id"], body.role, pin_hash))
        _event(conn, session, "account_created", employee["badge_id"], body.role)
        return _need_account(conn, employee["badge_id"])

    return _write(request, session, ADMINS, work)


def _set_pin(conn, row, pin_hash: str, session: Session, detail: str | None = None) -> None:
    conn.execute("UPDATE accounts SET pin_hash = %s, failed_attempts = 0, locked_until = NULL, updated_at = now() "
                 "WHERE id = %s", (pin_hash, row["id"]))
    _event(conn, session, "pin_changed", row["badge_id"], detail)


@router.post("/accounts/me/pin")
def change_own_pin(body: OwnPinIn, request: Request, session: Session = Depends(current_session)) -> dict:
    """Signed-in person changes their own PIN; the current one must match (and counts toward the lockout).
    Every old token stops working with the old PIN, so this answers with a fresh one for this person's session."""
    fresh = None
    authz.require(session, roles=EVERY_ROLE, areas=EVERY_AREA)
    now = datetime.now(timezone.utc)
    error = None
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        authz.require_live(conn, session, roles=EVERY_ROLE)
        row = conn.execute(
            "SELECT a.id, a.role, a.pin_hash, a.failed_attempts, a.locked_until, e.badge_id FROM accounts a "
            "JOIN employees e ON e.id = a.employee_id WHERE a.id = %s FOR UPDATE OF a",
            (session.account_id,)).fetchone()
        if row is None:
            raise _fail(401, "Sign in again.")
        if row["locked_until"] and row["locked_until"] > now:
            raise _fail(423, f"Account locked until {row['locked_until'].isoformat()}.")
        if not security.verify_pin(body.current_pin, row["pin_hash"]):
            attempts = row["failed_attempts"] + 1
            if attempts >= MAX_FAILED_ATTEMPTS:
                until = now + timedelta(minutes=LOCK_MINUTES)
                conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = %s WHERE id = %s",
                             (until, row["id"]))
                _auth_log(conn, "locked", row["badge_id"], session.area, "", f"{attempts} wrong PINs")
                error = _fail(423, f"Account locked until {until.isoformat()}.")
            else:
                conn.execute("UPDATE accounts SET failed_attempts = %s WHERE id = %s", (attempts, row["id"]))
                _auth_log(conn, "failed", row["badge_id"], session.area, "", "wrong PIN (change PIN)")
                error = _fail(401, "The current PIN is wrong.")
        elif problem := pin_problem(body.new_pin, row["role"]):
            error = _fail(422, problem)
        else:
            new_hash = security.hash_pin(body.new_pin)
            _set_pin(conn, row, new_hash, session, "own PIN")
            fresh = security.make_token(request.app.state.settings.jwt_secret, account_id=session.account_id,
                                        badge_id=session.badge_id, name=session.name, role=row["role"],
                                        area=session.area, pin_hash=new_hash)
    if error is not None:
        raise error
    return {"token": fresh}


@router.post("/accounts/{badge}/pin", status_code=204)
def set_pin(badge: str, body: PinIn, request: Request, session: Session = Depends(current_session)) -> None:
    """An administrator sets someone ELSE's PIN (also clears a lockout)."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _need_account(conn, badge, lock=True)
        if _same_person(session, row):
            raise _fail(409, "You cannot reset your own PIN without the current one; use Change my PIN.")
        if problem := pin_problem(body.pin, row["role"]):
            raise _fail(422, problem)
        _set_pin(conn, row, security.hash_pin(body.pin), session)

    _write(request, session, ADMINS, work)


@router.post("/accounts/{badge}/role", status_code=204)
def set_role(badge: str, body: RoleIn, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)
    if body.role not in ACCOUNT_ROLES:
        raise _fail(422, f"role must be one of {ACCOUNT_ROLES}.")

    def work(conn):
        row = _need_account(conn, badge, lock=True)
        if row["role"] == body.role:
            return
        if _same_person(session, row):
            raise _fail(409, "You cannot change your own role.")
        if body.role == "depot_manager":
            _depot_manager_needs_warehouse(_need_employee(conn, row["badge_id"]))
        promoting = body.role == "admin"
        new_hash = None
        if promoting:
            # An administrator PIN is longer than a till PIN: the old short one must not become an admin key.
            if not body.new_pin:
                raise _fail(422, f"Promoting someone to administrator needs a new PIN of at least {MIN_ADMIN_PIN} digits.")
            if problem := pin_problem(body.new_pin, "admin"):
                raise _fail(422, problem)
            old = conn.execute("SELECT pin_hash FROM accounts WHERE id = %s", (row["id"],)).fetchone()["pin_hash"]
            if security.verify_pin(body.new_pin, old):
                raise _fail(422, "The new administrator PIN must differ from the current PIN.")
            new_hash = security.hash_pin(body.new_pin)
        if row["role"] == "admin":
            _guard_last_admin(conn, row["employee_id"])
        if new_hash is not None:
            conn.execute("UPDATE accounts SET role = %s, pin_hash = %s, failed_attempts = 0, locked_until = NULL, "
                         "updated_at = now() WHERE id = %s", (body.role, new_hash, row["id"]))
        else:
            conn.execute("UPDATE accounts SET role = %s, updated_at = now() WHERE id = %s", (body.role, row["id"]))
        _event(conn, session, "account_changed", row["badge_id"],
               f"role {row['role']} -> {body.role}" + (" · new PIN set" if new_hash else ""))

    _write(request, session, ADMINS, work)


@router.post("/accounts/{badge}/active", status_code=204)
def set_active(badge: str, body: ActiveIn, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _need_account(conn, badge, lock=True)
        if not body.active:
            if _same_person(session, row):
                raise _fail(409, "You cannot switch off your own account.")
            if row["role"] == "admin":
                _guard_last_admin(conn, row["employee_id"])
        conn.execute("UPDATE accounts SET is_active = %s, updated_at = now() WHERE id = %s", (body.active, row["id"]))
        _event(conn, session, "account_changed", row["badge_id"], "switched on" if body.active else "switched off")

    _write(request, session, ADMINS, work)


@router.post("/accounts/{badge}/unlock", status_code=204)
def unlock(badge: str, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _need_account(conn, badge, lock=True)
        conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = NULL WHERE id = %s", (row["id"],))
        _event(conn, session, "unlocked", row["badge_id"])

    _write(request, session, ADMINS, work)


@router.delete("/accounts/{badge}", status_code=204)
def delete_account(badge: str, request: Request, session: Session = Depends(current_session)) -> None:
    """Remove the account (the employee stays in Workforce)."""
    authz.require(session, roles=ADMINS, areas=ADMIN_AREA)

    def work(conn):
        row = _need_account(conn, badge, lock=True)
        if _same_person(session, row):
            raise _fail(409, "You cannot remove your own account.")
        if row["role"] == "admin":
            _guard_last_admin(conn, row["employee_id"])
        conn.execute("DELETE FROM accounts WHERE id = %s", (row["id"],))
        _audit(conn, session, "deleted", "account", row["badge_id"], row["name"])
        _event(conn, session, "account_changed", row["badge_id"], "account removed")

    _write(request, session, ADMINS, work)
