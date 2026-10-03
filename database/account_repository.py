"""Sign-in accounts and the sign-in audit trail - the only place SQL for
`accounts` and `auth_events` lives. The rules (roles, what each may open,
PIN format, hashing, lockout numbers) are in shared/auth.py.

An account is an employee plus a role and a PIN hash. authenticate() is
the one door every app goes through: it checks the PIN, the lockout, that
the account and employee are active, and that the role may open that part
of the system, logs the outcome, and hands back a shared.auth.Session.
Only a correct PIN reveals whether the account is disabled or the wrong
role; a wrong badge and a wrong PIN get the same answer.

PIN hashing is deliberately slow, so it runs before the write lock is
taken; the counters are then updated in a short BEGIN IMMEDIATE.

Nothing here lets the system end up with no active administrator
(LastAdminError): demoting, switching off, or deleting the last one is
refused - otherwise nobody could open Admin to fix it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from database.connection import connection_scope
from database.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    AccountNotFoundError,
    AuthError,
    EmployeeNotFoundError,
    LastAdminError,
    NotAllowedError,
    SignInFailedError,
)
from shared import auth, recovery_code, security_question
from shared.auth import Actor, Session
from shared.formatting import local_datetime_text, parse_db_timestamp, to_db_timestamp
from shared.models import Account

_SELECT = (
    "SELECT a.id, a.role, a.pin_hash, a.is_active, a.failed_attempts, a.locked_until, a.last_sign_in_at, "
    "e.id AS employee_id, e.badge_id, e.name, e.is_active AS employee_active, e.location_type, e.location_name "
    "FROM accounts a JOIN employees e ON e.id = a.employee_id"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_account(row: sqlite3.Row) -> Account:
    return Account(
        id=row["id"],
        badge_id=row["badge_id"],
        name=row["name"],
        role=row["role"],
        is_active=bool(row["is_active"]),
        employee_active=bool(row["employee_active"]),
        location_type=row["location_type"],
        location_name=row["location_name"],
        failed_attempts=row["failed_attempts"],
        locked_until=row["locked_until"],
        last_sign_in_at=row["last_sign_in_at"],
    )


def _log(conn, event: str, badge_id: str | None, area: str | None = None, terminal: str | None = None,
         detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO auth_events (badge_id, event, area, terminal, detail) VALUES (?, ?, ?, ?, ?)",
        (badge_id, event, area, terminal, detail),
    )


def _fetch(conn, badge_id: str) -> sqlite3.Row | None:
    return conn.execute(_SELECT + " WHERE e.badge_id = ?", (badge_id,)).fetchone()


def _write(fn):
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(conn)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return result


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY or not _DUMMY[0].startswith(f"pbkdf2_sha256${auth.PBKDF2_ITERATIONS}$"):
        _DUMMY[:] = [auth.hash_pin("not-a-pin")]
    return _DUMMY[0]


def is_locked(account: Account, now: datetime | None = None) -> bool:
    if not account.locked_until:
        return False
    return parse_db_timestamp(account.locked_until) > (now or _now())


# --- the last-administrator guard ------------------------------------------

def _active_admin_employee_ids(conn) -> set[int]:
    rows = conn.execute(
        "SELECT a.employee_id FROM accounts a JOIN employees e ON e.id = a.employee_id "
        "WHERE a.role = 'admin' AND a.is_active = 1 AND e.is_active = 1"
    ).fetchall()
    return {row[0] for row in rows}


def guard_last_admin(conn, employee_id: int) -> None:
    """Raise LastAdminError if `employee_id` is the only active
    administrator. For employee_repository's deactivate/delete too."""
    if _active_admin_employee_ids(conn) == {employee_id}:
        raise LastAdminError()


# --- reads -------------------------------------------------------------------

def admin_exists() -> bool:
    """Whether anyone can open Admin - False only on a fresh system, which
    is when Admin shows its "create the first administrator" screen."""
    with connection_scope() as conn:
        return bool(_active_admin_employee_ids(conn))


def get(badge_id: str) -> Account:
    with connection_scope() as conn:
        row = _fetch(conn, badge_id)
    if row is None:
        raise AccountNotFoundError(badge_id)
    return _row_to_account(row)


def list_accounts() -> list[Account]:
    with connection_scope() as conn:
        rows = conn.execute(_SELECT + " ORDER BY e.name").fetchall()
    return [_row_to_account(row) for row in rows]


def employees_without_account() -> list[tuple[str, str]]:
    """(badge_id, name) of active employees who can be given an account."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT badge_id, name FROM employees e WHERE is_active = 1 "
            "AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.employee_id = e.id) ORDER BY name"
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


def list_events(limit: int = 100) -> list[dict]:
    """Newest first: created_at, badge_id, name (if the badge is a known
    employee), event, area, terminal, detail."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT v.created_at, v.badge_id, e.name, v.event, v.area, v.terminal, v.detail "
            "FROM auth_events v LEFT JOIN employees e ON e.badge_id = v.badge_id "
            "ORDER BY v.created_at DESC, v.id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


# --- creating accounts -------------------------------------------------------

def _check_pin(pin: str, role: str) -> None:
    if role not in auth.ROLES:
        raise ValueError(f"Unknown role {role!r}")
    problem = auth.pin_problem(pin, role)
    if problem:
        raise ValueError(problem)


def create_account(badge_id: str, role: str, pin: str, by: Actor | None = None) -> Account:
    """Give an existing, active employee a sign-in account. Raises
    ValueError (bad PIN / role, already has an account, inactive employee),
    EmployeeNotFoundError."""
    badge_id = (badge_id or "").strip()
    _check_pin(pin, role)
    pin_hash = auth.hash_pin(pin)

    def run(conn):
        employee = conn.execute("SELECT id, is_active FROM employees WHERE badge_id = ?", (badge_id,)).fetchone()
        if employee is None:
            raise EmployeeNotFoundError(badge_id)
        if not employee["is_active"]:
            raise ValueError(f"{badge_id} is marked inactive in Workforce.")
        if conn.execute("SELECT 1 FROM accounts WHERE employee_id = ?", (employee["id"],)).fetchone():
            raise ValueError(f"{badge_id} already has an account.")
        conn.execute("INSERT INTO accounts (employee_id, role, pin_hash) VALUES (?, ?, ?)",
                     (employee["id"], role, pin_hash))
        _log(conn, "account_created", badge_id, detail=f"{auth.role_label(role)}" + (f" · by {by.label}" if by else ""))

    _write(run)
    return get(badge_id)


def create_first_admin(badge_id: str, name: str, pin: str, terminal: str = "Admin") -> Session:
    """The one-time setup on a fresh system: make `badge_id` the first
    administrator - an existing employee, or a new one named `name` - and
    sign them in. Refused once any active administrator exists."""
    badge_id, name = (badge_id or "").strip(), (name or "").strip()
    if not badge_id:
        raise ValueError("Enter a badge ID.")
    _check_pin(pin, "admin")
    pin_hash = auth.hash_pin(pin)

    def run(conn):
        if _active_admin_employee_ids(conn):
            raise AuthError("An administrator already exists - sign in instead.")
        employee = conn.execute("SELECT id, is_active FROM employees WHERE badge_id = ?", (badge_id,)).fetchone()
        if employee is None:
            if not name:
                raise ValueError("Enter your name.")
            cursor = conn.execute(
                "INSERT INTO employees (badge_id, name, title, role, location_type, location_name) "
                "VALUES (?, ?, 'Administrator', 'Management', 'Dealership', 'Head office')",
                (badge_id, name),
            )
            employee_id = cursor.lastrowid
        else:
            employee_id = employee["id"]
            conn.execute("UPDATE employees SET is_active = 1 WHERE id = ?", (employee_id,))
        existing = conn.execute("SELECT id FROM accounts WHERE employee_id = ?", (employee_id,)).fetchone()
        if existing:
            conn.execute("UPDATE accounts SET role = 'admin', pin_hash = ?, is_active = 1, failed_attempts = 0, "
                         "locked_until = NULL WHERE id = ?", (pin_hash, existing["id"]))
        else:
            conn.execute("INSERT INTO accounts (employee_id, role, pin_hash) VALUES (?, 'admin', ?)",
                         (employee_id, pin_hash))
        _log(conn, "account_created", badge_id, auth.AREA_ADMIN, terminal, "First administrator")

    _write(run)
    return authenticate(badge_id, pin, auth.AREA_ADMIN, terminal)


# --- signing in ---------------------------------------------------------------

def authenticate(badge_id: str, pin: str, area: str, terminal: str, event: str = "sign_in") -> Session:
    """Check a badge + PIN for `area` and return the Session. Raises
    SignInFailedError, AccountLockedError, AccountDisabledError,
    NotAllowedError. Every outcome is logged."""
    badge_id = (badge_id or "").strip()
    with connection_scope() as conn:
        row = _fetch(conn, badge_id)
    now = _now()
    if row is None:
        auth.verify_pin(pin or "", _dummy_hash())  # the same work as a real check: no timing hint

        def unknown(conn):
            _log(conn, "failed", badge_id or None, area, terminal, "unknown badge")

        _write(unknown)
        raise SignInFailedError()

    account = _row_to_account(row)
    if is_locked(account, now):
        _write(lambda conn: _log(conn, "failed", badge_id, area, terminal, "locked"))
        raise AccountLockedError(local_datetime_text(account.locked_until))

    if not auth.verify_pin(pin or "", row["pin_hash"]):
        def wrong(conn):
            attempts = conn.execute("SELECT failed_attempts FROM accounts WHERE id = ?", (account.id,)).fetchone()[0] + 1
            if attempts >= auth.MAX_FAILED_ATTEMPTS:
                until = to_db_timestamp(now + timedelta(minutes=auth.LOCK_MINUTES))
                conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = ? WHERE id = ?", (until, account.id))
                _log(conn, "locked", badge_id, area, terminal, f"{attempts} wrong PINs")
                return until
            conn.execute("UPDATE accounts SET failed_attempts = ? WHERE id = ?", (attempts, account.id))
            _log(conn, "failed", badge_id, area, terminal, "wrong PIN")
            return auth.MAX_FAILED_ATTEMPTS - attempts

        outcome = _write(wrong)
        if isinstance(outcome, str):
            raise AccountLockedError(local_datetime_text(outcome))
        raise SignInFailedError(outcome)

    if not account.is_active or not account.employee_active:
        _write(lambda conn: _log(conn, "refused", badge_id, area, terminal, "account switched off"))
        raise AccountDisabledError()
    if not auth.can_open(account.role, area):
        _write(lambda conn: _log(conn, "refused", badge_id, area, terminal, f"{auth.role_label(account.role)} role"))
        raise NotAllowedError(auth.role_label(account.role), auth.AREA_LABELS.get(area, area))

    signed_in_at = to_db_timestamp(now)

    def ok(conn):
        conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = NULL, last_sign_in_at = ? WHERE id = ?",
                     (signed_in_at, account.id))
        _log(conn, event, badge_id, area, terminal)

    _write(ok)
    return Session(account_id=account.id, badge_id=account.badge_id, name=account.name, role=account.role,
                   signed_in_at=signed_in_at, area=area, terminal=terminal)


def confirm_pin(session: Session, pin: str) -> None:
    """Re-check the signed-in person's PIN (the Manager Portal gate). Same
    lockout and logging as authenticate(); raises the same errors."""
    authenticate(session.badge_id, pin, session.area, session.terminal, event="pin_confirmed")


def sign_out(session: Session) -> None:
    _write(lambda conn: _log(conn, "sign_out", session.badge_id, session.area, session.terminal))


# --- managing accounts ------------------------------------------------------------

def _account_row(conn, badge_id: str) -> sqlite3.Row:
    row = _fetch(conn, badge_id)
    if row is None:
        raise AccountNotFoundError(badge_id)
    return row


def set_pin(badge_id: str, new_pin: str, by: Actor | None = None) -> None:
    """An administrator sets a new PIN (also clears a lockout)."""
    account = get(badge_id)
    _check_pin(new_pin, account.role)
    pin_hash = auth.hash_pin(new_pin)

    def run(conn):
        conn.execute("UPDATE accounts SET pin_hash = ?, failed_attempts = 0, locked_until = NULL, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (pin_hash, account.id))
        _log(conn, "pin_changed", badge_id, detail=f"by {by.label}" if by else None)

    _write(run)


def list_active_admins() -> list[Account]:
    """Active administrator accounts, for the "forgot PIN" screen."""
    return [a for a in list_accounts() if a.role == "admin" and a.is_active and a.employee_active]


def reset_admin_access(badge_id: str, new_pin: str, terminal: str = "Admin") -> None:
    """Set a new PIN for an administrator and clear any lockout, WITHOUT the
    old PIN. Only for the recovery flow (shared/recovery.py), which has
    already checked proof of access to the data folder. Refuses anyone who
    is not an active administrator, and is written to the sign-in log."""
    account = get(badge_id)
    if account.role != "admin" or not account.is_active:
        raise AuthError("That account is not an active administrator.")
    _check_pin(new_pin, "admin")
    pin_hash = auth.hash_pin(new_pin)

    def run(conn):
        conn.execute("UPDATE accounts SET pin_hash = ?, failed_attempts = 0, locked_until = NULL, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (pin_hash, account.id))
        _log(conn, "pin_reset", badge_id, auth.AREA_ADMIN, terminal, "Reset with the recovery file")

    _write(run)


# --- recovery code ---------------------------------------------------------------
# One code per installation, kept (hashed) in app_settings. Whoever holds it
# can set a new administrator PIN from the sign-in screen, so it is shown
# once, replaced on request, and guessing it is rate-limited.

_RC_HASH, _RC_FAILED, _RC_LOCKED = "recovery.code_hash", "recovery.failed", "recovery.locked_until"
_SQ_QUESTION, _SQ_HASH = "recovery.question", "recovery.answer_hash"


def has_recovery_code() -> bool:
    with connection_scope() as conn:
        row = conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (_RC_HASH,)).fetchone()
    return row is not None


def create_recovery_code(by: Session) -> str:
    """Make (or replace) the recovery code and return it - the only time it
    can be read. Administrators only; the old code stops working."""
    if by.role != "admin":
        raise AuthError("Only an administrator can create the recovery code.")
    actor = by.actor
    code = recovery_code.generate()
    code_hash = auth.hash_pin(recovery_code.normalize(code))

    def run(conn):
        for key, value in ((_RC_HASH, code_hash), (_RC_FAILED, "0"), (_RC_LOCKED, "")):
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                         "SET value = excluded.value, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                         (key, value))
        _log(conn, "recovery_code_created", actor.badge_id, auth.AREA_ADMIN, None, "New recovery code")

    _write(run)
    return code


def reset_with_recovery_code(code: str, badge_id: str, new_pin: str, terminal: str = "Admin") -> None:
    """Set a new PIN for administrator `badge_id` using the recovery code.
    Wrong codes are counted; after MAX_FAILED_ATTEMPTS recovery locks for
    LOCK_MINUTES. Raises AuthError with a message fit to show."""
    normalized = recovery_code.normalize(code)
    _reset_with_secret(
        _RC_HASH, normalized, len(normalized) == recovery_code.CODE_LENGTH, badge_id, new_pin, terminal,
        "Reset with the recovery code", "This installation has no recovery code yet.",
        "That recovery code is not right.",
    )


def get_security_question() -> str | None:
    """The question to ask on the sign-in screen, or None if none is set."""
    with connection_scope() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (_SQ_QUESTION,)).fetchone()
    return row["value"] if row and row["value"] else None


def set_security_question(session: Session, current_pin: str, question: str, answer: str) -> None:
    """An administrator sets (or replaces) the installation's security
    question. Needs their current PIN, so a walk-up can't swap it."""
    if session.role != "admin":
        raise AuthError("Only an administrator can set the security question.")
    question, answer = security_question.validate(question, answer)
    row = get(session.badge_id)
    with connection_scope() as conn:
        stored = conn.execute("SELECT pin_hash FROM accounts WHERE id = ?", (row.id,)).fetchone()[0]
    if not auth.verify_pin(current_pin or "", stored):
        raise SignInFailedError()
    answer_hash = auth.hash_pin(security_question.normalize_answer(answer))

    def run(conn):
        for key, value in ((_SQ_QUESTION, question), (_SQ_HASH, answer_hash), (_RC_FAILED, "0"), (_RC_LOCKED, "")):
            conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                         "SET value = excluded.value, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                         (key, value))
        _log(conn, "security_question_set", session.badge_id, auth.AREA_ADMIN, session.terminal, None)

    _write(run)


def reset_with_security_answer(answer: str, badge_id: str, new_pin: str, terminal: str = "Admin") -> None:
    """Set a new PIN for administrator `badge_id` after the security
    question is answered. Shares the wrong-guess counter (and lock) with
    the recovery code, so trying both doesn't double the guesses."""
    normalized = security_question.normalize_answer(answer)
    _reset_with_secret(
        _SQ_HASH, normalized, len(normalized) >= security_question.MIN_ANSWER_LENGTH, badge_id, new_pin, terminal,
        "Reset by answering the security question", "No security question has been set yet.",
        "That answer is not right.",
    )


def _reset_with_secret(hash_key, secret, plausible, badge_id, new_pin, terminal, success_detail, none_message,
                       wrong_message) -> None:
    _check_pin(new_pin, "admin")
    new_hash = auth.hash_pin(new_pin)

    def settings(conn):
        return {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM app_settings WHERE key IN (?, ?, ?)", (hash_key, _RC_FAILED, _RC_LOCKED))}

    def put(conn, key, value):
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                     "SET value = excluded.value, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')", (key, value))

    def run(conn):
        now = _now()
        values = settings(conn)
        stored = values.get(hash_key)
        if not stored:
            return "none", None
        locked = values.get(_RC_LOCKED) or ""
        if locked and parse_db_timestamp(locked) > now:
            return "locked", locked
        # Always do the (slow) hash work, so a wrong secret and a right one cost the same.
        ok = auth.verify_pin(secret, stored) and plausible
        if not ok:
            failed = int(values.get(_RC_FAILED) or 0) + 1
            expiry = recovery_code.lock_expiry(failed, now)
            put(conn, _RC_FAILED, "0" if expiry else str(failed))
            put(conn, _RC_LOCKED, to_db_timestamp(expiry) if expiry else "")
            _log(conn, "recovery_failed", badge_id, auth.AREA_ADMIN, terminal,
                 "Locked for %d minutes" % recovery_code.LOCK_MINUTES if expiry else None)
            return "wrong", None
        row = _account_row(conn, badge_id)
        if row["role"] != "admin" or not row["is_active"]:
            return "not_admin", None
        conn.execute("UPDATE accounts SET pin_hash = ?, failed_attempts = 0, locked_until = NULL, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (new_hash, row["id"]))
        put(conn, _RC_FAILED, "0")
        put(conn, _RC_LOCKED, "")
        _log(conn, "pin_reset", badge_id, auth.AREA_ADMIN, terminal, success_detail)
        return "ok", None

    status, extra = _write(run)  # failed guesses must be COMMITTED, so the error is raised after
    if status == "ok":
        return
    if status == "none":
        raise AuthError(none_message)
    if status == "locked":
        raise AuthError(f"Too many wrong tries - recovery is locked until {local_datetime_text(extra)}.")
    if status == "not_admin":
        raise AuthError("That account is not an active administrator.")
    raise AuthError(wrong_message)


def change_own_pin(session: Session, current_pin: str, new_pin: str) -> None:
    """Signed-in person changes their own PIN; the current one must match."""
    row = get(session.badge_id)
    with connection_scope() as conn:
        stored = conn.execute("SELECT pin_hash FROM accounts WHERE id = ?", (row.id,)).fetchone()[0]
    if not auth.verify_pin(current_pin or "", stored):
        raise SignInFailedError()
    set_pin(session.badge_id, new_pin, by=session.actor)


def set_role(badge_id: str, role: str, by: Actor | None = None) -> None:
    if role not in auth.ROLES:
        raise ValueError(f"Unknown role {role!r}")

    def run(conn):
        row = _account_row(conn, badge_id)
        if row["role"] == role:
            return
        if row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        conn.execute("UPDATE accounts SET role = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                     (role, row["id"]))
        _log(conn, "account_changed", badge_id,
             detail=f"role {auth.role_label(row['role'])} -> {auth.role_label(role)}" + (f" · by {by.label}" if by else ""))

    _write(run)


def set_active(badge_id: str, active: bool, by: Actor | None = None) -> None:
    def run(conn):
        row = _account_row(conn, badge_id)
        if not active and row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        conn.execute("UPDATE accounts SET is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                     (int(bool(active)), row["id"]))
        _log(conn, "account_changed", badge_id,
             detail=("switched on" if active else "switched off") + (f" · by {by.label}" if by else ""))

    _write(run)


def unlock(badge_id: str, by: Actor | None = None) -> None:
    def run(conn):
        row = _account_row(conn, badge_id)
        conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (row["id"],))
        _log(conn, "unlocked", badge_id, detail=f"by {by.label}" if by else None)

    _write(run)


def delete(badge_id: str, by: Actor | None = None) -> None:
    """Remove the account (the employee stays in Workforce)."""
    def run(conn):
        row = _account_row(conn, badge_id)
        if row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        conn.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
        _log(conn, "account_changed", badge_id, detail="account removed" + (f" · by {by.label}" if by else ""))

    _write(run)
