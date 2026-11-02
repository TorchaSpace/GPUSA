"""Server versions of the employee, attendance and account repository functions.

employee_repository, attendance_repository and account_repository call these instead of
SQLite when database.remote is active. Same signatures and return types as the local
functions; the rules (last administrator, no self-service resets, PIN strength, one open
shift per badge, depot-scoped punches) are checked on the server and its refusals come
back with the server's own words - the common ones as the same exceptions the screens
already catch."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from database import remote
from database.exceptions import (
    AccountNotFoundError,
    AlreadyCheckedInError,
    DuplicateBadgeIdError,
    EmployeeNotFoundError,
    LastAdminError,
    NoOpenAttendanceRecordError,
)
from database.remote_stock import db_timestamp
from shared.auth import Actor, normalize_badge_id
from shared.models import Account, Employee


def _badge(text: str) -> str:
    return normalize_badge_id(text)


def _call(method: str, url: str, body: dict | None = None, *, errors: dict | None = None):
    """remote.call, with the server's well-known 409 answers turned into the desktop's own exceptions."""
    try:
        return remote.call(method, url, body, errors=errors)
    except remote.ServerRefusedError as exc:
        text = str(exc).lower()
        if exc.status == 409:
            if "exists already" in text and "employee" in text:
                raise DuplicateBadgeIdError(_badge((body or {}).get("badge_id", ""))) from None
            if "already checked in" in text:
                raise AlreadyCheckedInError(_badge((body or {}).get("badge_id", ""))) from None
            if "not checked in" in text and method == "POST":
                raise NoOpenAttendanceRecordError(_badge((body or {}).get("badge_id", ""))) from None
            if "only active administrator" in text:
                raise LastAdminError() from None
        raise


# --- employees ---------------------------------------------------------------------------

def _employee(data: dict) -> Employee:
    return Employee(id=int(data["id"]), badge_id=data["badge_id"], name=data["name"], title=data.get("title"),
                    role=data["role"], location_type=data["location_type"], location_name=data["location_name"],
                    is_active=bool(data["is_active"]))


def get_employee(badge_id: str) -> Employee:
    badge = _badge(badge_id)
    return _employee(_call("GET", remote.path("employees", badge),
                           errors={404: lambda: EmployeeNotFoundError(badge)}))


def list_employees() -> list[Employee]:
    return [_employee(row) for row in _call("GET", "/employees")]


def employees_for_roster() -> list[dict]:
    """The roster's input: every employee, in name order, as plain dicts."""
    return [dict(row) for row in sorted(_call("GET", "/employees"), key=lambda r: r["name"])]


def location_choices(include_codes: bool = False) -> dict[str, list[str]]:
    places = _call("GET", "/employees/locations")
    names = {kind: [p["name"] for p in found] for kind, found in places.items()}
    if include_codes:
        return {kind: names[kind] + [p["code"] for p in places[kind]] for kind in names}
    return names


def create_employee(employee: Employee) -> None:
    _call("POST", "/employees", _employee_body(employee, with_badge=True))


def update_employee(employee: Employee) -> None:
    badge = _badge(employee.badge_id)
    _call("PATCH", remote.path("employees", badge), _employee_body(employee, with_badge=False),
          errors={404: lambda: EmployeeNotFoundError(badge)})


def delete_employee(badge_id: str) -> None:
    badge = _badge(badge_id)
    _call("DELETE", remote.path("employees", badge), errors={404: lambda: EmployeeNotFoundError(badge)})


def _employee_body(employee: Employee, *, with_badge: bool) -> dict:
    body = {"name": employee.name, "title": employee.title, "role": employee.role,
            "location_type": employee.location_type, "location_name": employee.location_name,
            "is_active": bool(employee.is_active)}
    if with_badge:
        body["badge_id"] = employee.badge_id
    return body


# --- attendance --------------------------------------------------------------------------

def check_in(badge_id: str, note: str | None, warehouse_code: str | None) -> int:
    badge = _badge(badge_id)
    result = _call("POST", "/attendance/check-in", {"badge_id": badge, "note": note, "warehouse_code": warehouse_code},
                   errors={404: lambda: EmployeeNotFoundError(badge)})
    return int(result["id"])


def check_out(badge_id: str, note: str | None, warehouse_code: str | None) -> int:
    badge = _badge(badge_id)
    result = _call("POST", "/attendance/check-out", {"badge_id": badge, "note": note, "warehouse_code": warehouse_code},
                   errors={404: lambda: EmployeeNotFoundError(badge)})
    return int(result["id"])


def operator_actor(badge_id: str, warehouse_code: str | None) -> Actor:
    badge = _badge(badge_id)
    query = {"badge_id": badge}
    if warehouse_code:
        query["warehouse_code"] = warehouse_code
    got = _call("GET", "/attendance/operator?" + urlencode(query), errors={404: lambda: EmployeeNotFoundError(badge)})
    return Actor(got["badge_id"], got["name"])


def open_shifts() -> list[dict]:
    return [{**row, "check_in_at": db_timestamp(row["check_in_at"])} for row in _call("GET", "/attendance/open")]


def attendance_records(start: datetime, end: datetime) -> list[dict]:
    """Records touching [start, end), with db-format stamps (the shape the local queries return)."""
    rows = _call("GET", "/attendance/records?" + urlencode({"start": start.isoformat(), "end": end.isoformat()}))
    return [{**row, "check_in_at": db_timestamp(row["check_in_at"]), "check_out_at": db_timestamp(row["check_out_at"])}
            for row in rows]


# --- accounts ----------------------------------------------------------------------------

def _account(data: dict) -> Account:
    return Account(id=int(data["id"]), badge_id=data["badge_id"], name=data["name"], role=data["role"],
                   is_active=bool(data["is_active"]), employee_active=bool(data["employee_active"]),
                   location_type=data["location_type"], location_name=data["location_name"],
                   failed_attempts=int(data.get("failed_attempts") or 0),
                   locked_until=db_timestamp(data.get("locked_until")),
                   last_sign_in_at=db_timestamp(data.get("last_sign_in_at")))


def list_accounts() -> list[Account]:
    return [_account(row) for row in _call("GET", "/accounts")]


def get_account(badge_id: str) -> Account:
    badge = _badge(badge_id)
    for account in list_accounts():
        if _badge(account.badge_id) == badge:
            return account
    raise AccountNotFoundError(badge)


def employees_without_account() -> list[tuple[str, str]]:
    return [(row["badge_id"], row["name"]) for row in _call("GET", "/accounts/without")]


def list_events(limit: int) -> list[dict]:
    rows = _call("GET", "/accounts/events?" + urlencode({"limit": int(limit)}))
    return [{**row, "created_at": db_timestamp(row["created_at"])} for row in rows]


def create_account(badge_id: str, role: str, pin: str) -> Account:
    badge = _badge(badge_id)
    return _account(_call("POST", "/accounts", {"badge_id": badge, "role": role, "pin": pin},
                          errors={404: lambda: EmployeeNotFoundError(badge)}))


def set_pin(badge_id: str, new_pin: str) -> None:
    badge = _badge(badge_id)
    _call("POST", remote.path("accounts", badge, "pin"), {"pin": new_pin},
          errors={404: lambda: AccountNotFoundError(badge)})


def change_own_pin(current_pin: str, new_pin: str) -> None:
    _call("POST", "/accounts/me/pin", {"current_pin": current_pin, "new_pin": new_pin})


def set_role(badge_id: str, role: str, new_pin: str | None) -> None:
    badge = _badge(badge_id)
    _call("POST", remote.path("accounts", badge, "role"), {"role": role, "new_pin": new_pin},
          errors={404: lambda: AccountNotFoundError(badge)})


def set_active(badge_id: str, active: bool) -> None:
    badge = _badge(badge_id)
    _call("POST", remote.path("accounts", badge, "active"), {"active": bool(active)},
          errors={404: lambda: AccountNotFoundError(badge)})


def unlock(badge_id: str) -> None:
    badge = _badge(badge_id)
    _call("POST", remote.path("accounts", badge, "unlock"), {}, errors={404: lambda: AccountNotFoundError(badge)})


def delete_account(badge_id: str) -> None:
    badge = _badge(badge_id)
    _call("DELETE", remote.path("accounts", badge), errors={404: lambda: AccountNotFoundError(badge)})
