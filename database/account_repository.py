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

from shared.i18n import UserError
from database.connection import connection_scope
from database.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    AccountNotFoundError,
    AuthError,
    EmployeeNotFoundError,
    LastAdminError,
    NotAllowedError,
    SelfActionError,
    SessionInvalidError,
    SignInFailedError,
    localized_auth,
)
from database import employee_repository
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
    """The account row for a typed/scanned badge: any letter case, stray
    spaces ignored (rows saved in lower case before badges were upper-cased
    still match; an exact match wins)."""
    raw = (badge_id or "").strip()
    return conn.execute(
        _SELECT + " WHERE e.badge_id = ? OR upper(e.badge_id) = ? ORDER BY (e.badge_id = ?) DESC, e.id LIMIT 1",
        (raw, raw.upper(), raw.upper()),
    ).fetchone()


def live_role(conn, badge_id: str) -> str | None:
    """The role the badge's account holds RIGHT NOW (read from the open
    connection, so it is part of the caller's transaction), or None when
    there is no account, it is switched off, or its employee is inactive."""
    row = _fetch(conn, badge_id)
    if row is None or not row["is_active"] or not row["employee_active"]:
        return None
    return row["role"]


def _fetch_by_id(conn, account_id: int) -> sqlite3.Row | None:
    return conn.execute(_SELECT + " WHERE a.id = ?", (account_id,)).fetchone()


def _live_admin(conn, badge_id: str) -> sqlite3.Row:
    """The CURRENT row of the administrator doing something sensitive -
    re-read from the database, never trusted from a Session/Actor object
    that may be hours old. SessionInvalidError if they are gone, switched
    off, or no longer an administrator."""
    row = _fetch(conn, badge_id)
    if row is None or not row["is_active"] or not row["employee_active"] or row["role"] != "admin":
        raise SessionInvalidError()
    return row


def _check_actor(conn, by: Actor | None, target_badge: str, *, allow_self: bool = False) -> bool:
    """Common guard for admin actions that know who is acting (`by`).
    Returns True if `by` is the target account itself. `by=None` (setup
    scripts, tests) skips the checks. With allow_self, acting on your own
    account only needs it to be active (changing your own PIN)."""
    if by is None:
        return False
    same = auth.normalize_badge_id(by.badge_id) == auth.normalize_badge_id(target_badge)
    if same and allow_self:
        row = _fetch(conn, by.badge_id)
        if row is None or not row["is_active"] or not row["employee_active"]:
            raise SessionInvalidError()
        return True
    _live_admin(conn, by.badge_id)
    return same


def is_session_valid(session: Session | None) -> bool:
    """Whether `session` still stands: its account exists, is switched on,
    belongs to an active employee, still has the role the person signed in
    with, and that role may still open the area. One cheap read - call it
    before anything that must not be done by someone who has since been
    switched off (POS: before finalising a sale)."""
    if session is None:
        return False
    try:
        with connection_scope() as conn:
            row = _fetch_by_id(conn, session.account_id)
    except sqlite3.Error:
        return False
    return (
        row is not None
        and bool(row["is_active"])
        and bool(row["employee_active"])
        and row["role"] == session.role
        and auth.can_open(row["role"], session.area)
    )


def require_actor_allowed(conn: sqlite3.Connection, actor: Actor | None, area: str) -> None:
    """SessionInvalidError unless `actor` - identified by badge, which is all
    an Actor carries - still has an account that is switched on, belongs to
    an active employee, and has a role that may open `area` TODAY. Reads on
    the caller's connection, so a repository can check it inside the very
    transaction it is about to write in (POS: finalising a sale). No actor
    (setup scripts, tests) skips the check, as everywhere else."""
    if actor is None:
        return
    row = _fetch(conn, actor.badge_id)
    if row is None or not row["is_active"] or not row["employee_active"] or not auth.can_open(row["role"], area):
        raise SessionInvalidError()


def require_valid_session(session: Session | None) -> None:
    """is_session_valid() that raises SessionInvalidError instead."""
    if not is_session_valid(session):
        raise SessionInvalidError()


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
        raise AccountNotFoundError(auth.normalize_badge_id(badge_id))
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
    badge_id = auth.normalize_badge_id(badge_id)
    _check_pin(pin, role)
    pin_hash = auth.hash_pin(pin)

    def run(conn):
        nonlocal badge_id
        _check_actor(conn, by, badge_id)
        employee = employee_repository.find_row(conn, badge_id)
        if employee is None:
            raise EmployeeNotFoundError(badge_id)
        badge_id = employee["badge_id"]
        if not employee["is_active"]:
            raise UserError("err.account_employee_inactive", badge=badge_id)
        if conn.execute("SELECT 1 FROM accounts WHERE employee_id = ?", (employee["id"],)).fetchone():
            raise UserError("err.account_exists", badge=badge_id)
        conn.execute("INSERT INTO accounts (employee_id, role, pin_hash) VALUES (?, ?, ?)",
                     (employee["id"], role, pin_hash))
        _log(conn, "account_created", badge_id, detail=f"{auth.role_label(role)}" + (f" · by {by.label}" if by else ""))

    _write(run)
    return get(badge_id)


def create_first_admin(badge_id: str, name: str, pin: str, terminal: str = "Admin") -> Session:
    """The one-time setup on a fresh system: make `badge_id` the first
    administrator - an existing employee, or a new one named `name` - and
    sign them in. Refused once any active administrator exists."""
    badge_id, name = auth.normalize_badge_id(badge_id), " ".join((name or "").split())
    if not badge_id:
        raise UserError("err.badge_required")
    if len(badge_id) > employee_repository.MAX_BADGE_LENGTH:
        raise UserError("err.badge_too_long", n=employee_repository.MAX_BADGE_LENGTH)
    if len(name) > employee_repository.MAX_NAME_LENGTH:
        raise UserError("err.name_too_long", n=employee_repository.MAX_NAME_LENGTH)
    _check_pin(pin, "admin")
    pin_hash = auth.hash_pin(pin)

    def run(conn):
        nonlocal badge_id
        if _active_admin_employee_ids(conn):
            raise localized_auth("err.admin_exists")
        employee = employee_repository.find_row(conn, badge_id)
        if employee is not None:
            badge_id = employee["badge_id"]
        if employee is None:
            if not name:
                raise UserError("err.your_name_required")
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

def _verify_with_lockout(row: sqlite3.Row, pin: str, area: str, terminal: str, now: datetime) -> None:
    """The lockout and PIN check shared by authenticate() and the
    "type your PIN again" actions: raises AccountLockedError while locked,
    counts a wrong PIN (locking at MAX_FAILED_ATTEMPTS) and raises
    SignInFailedError - with the same bare message whoever the badge
    belongs to, so the attempts left are never revealed. Returns on a
    correct PIN."""
    account = _row_to_account(row)
    badge_id = row["badge_id"]
    if is_locked(account, now):
        _write(lambda conn: _log(conn, "failed", badge_id, area, terminal, "locked"))
        raise AccountLockedError(local_datetime_text(account.locked_until))

    if auth.verify_pin(pin or "", row["pin_hash"]):
        return

    def wrong(conn):
        attempts = conn.execute("SELECT failed_attempts FROM accounts WHERE id = ?", (account.id,)).fetchone()[0] + 1
        if attempts >= auth.MAX_FAILED_ATTEMPTS:
            until = to_db_timestamp(now + timedelta(minutes=auth.LOCK_MINUTES))
            conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = ? WHERE id = ?", (until, account.id))
            _log(conn, "locked", badge_id, area, terminal, f"{attempts} wrong PINs")
            return until
        conn.execute("UPDATE accounts SET failed_attempts = ? WHERE id = ?", (attempts, account.id))
        _log(conn, "failed", badge_id, area, terminal, "wrong PIN")
        return None

    until = _write(wrong)
    if until is not None:
        raise AccountLockedError(local_datetime_text(until))
    raise SignInFailedError()


def authenticate(badge_id: str, pin: str, area: str, terminal: str, event: str = "sign_in") -> Session:
    """Check a badge + PIN for `area` and return the Session. The badge is
    matched in any letter case. Raises SignInFailedError,
    AccountLockedError, AccountDisabledError, NotAllowedError. Every
    outcome is logged."""
    badge_id = auth.normalize_badge_id(badge_id)
    with connection_scope() as conn:
        row = _fetch(conn, badge_id)
    now = _now()
    if row is None:
        auth.verify_pin(pin or "", _dummy_hash())  # the same work as a real check: no timing hint

        def unknown(conn):
            _log(conn, "failed", badge_id or None, area, terminal, "unknown badge")

        _write(unknown)
        raise SignInFailedError()

    _verify_with_lockout(row, pin, area, terminal, now)
    account = _row_to_account(row)
    badge_id = account.badge_id

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


def _confirm_current_pin(session: Session, pin: str, *, admin: bool = False) -> sqlite3.Row:
    """For actions that ask for "your current PIN" (change PIN, security
    question): re-reads the account from the database (it must still be
    active - and an administrator if `admin`), then checks the PIN with the
    same lockout as sign-in, so a walk-up at an unlocked screen can't guess
    it for free. Returns the fresh account row."""
    with connection_scope() as conn:
        row = _fetch_by_id(conn, session.account_id)
    if row is None or not row["is_active"] or not row["employee_active"] or (admin and row["role"] != "admin"):
        raise SessionInvalidError()
    _verify_with_lockout(row, pin, session.area, session.terminal, _now())
    return row


def sign_out(session: Session) -> None:
    _write(lambda conn: _log(conn, "sign_out", session.badge_id, session.area, session.terminal))


# --- managing accounts ------------------------------------------------------------

def _account_row(conn, badge_id: str) -> sqlite3.Row:
    row = _fetch(conn, badge_id)
    if row is None:
        raise AccountNotFoundError(badge_id)
    return row


def set_pin(badge_id: str, new_pin: str, by: Actor | None = None) -> None:
    """An administrator sets a new PIN (also clears a lockout); a person
    may set their own (`by` is them - change_own_pin checks the old PIN
    first). With `by` given, the actor is re-checked in the database:
    someone else's PIN needs a CURRENT active administrator."""
    account = get(badge_id)
    _check_pin(new_pin, account.role)
    pin_hash = auth.hash_pin(new_pin)

    def run(conn):
        _check_actor(conn, by, account.badge_id, allow_self=True)
        row = _account_row(conn, account.badge_id)
        if row["role"] != account.role:  # changed while we were hashing
            _check_pin(new_pin, row["role"])
        conn.execute("UPDATE accounts SET pin_hash = ?, failed_attempts = 0, locked_until = NULL, "
                     "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (pin_hash, row["id"]))
        _log(conn, "pin_changed", row["badge_id"], detail=f"by {by.label}" if by else None)

    _write(run)


def list_active_admins() -> list[Account]:
    """Active administrator accounts, for the "forgot PIN" screen."""
    return [a for a in list_accounts() if a.role == "admin" and a.is_active and a.employee_active]


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
        raise localized_auth("err.recovery_admin_only")
    actor = by.actor
    code = recovery_code.generate()
    code_hash = auth.hash_pin(recovery_code.normalize(code))

    def run(conn):
        live = _fetch_by_id(conn, by.account_id)  # the session may be stale: ask the database
        if live is None or not live["is_active"] or not live["employee_active"] or live["role"] != "admin":
            raise SessionInvalidError()
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
        "Reset with the recovery code", "err.rc_none",
        "err.rc_wrong",
    )


def get_security_question() -> str | None:
    """The question to ask on the sign-in screen, or None if none is set."""
    with connection_scope() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (_SQ_QUESTION,)).fetchone()
    return row["value"] if row and row["value"] else None


# A security answer must be a little stronger than the question module's
# own minimum (3): it guards a full administrator reset.
MIN_SECURITY_ANSWER_LENGTH = max(4, security_question.MIN_ANSWER_LENGTH)


def validate_security_answer(question: str, answer: str) -> tuple[str, str]:
    """security_question.validate() plus the stricter answer length (counted
    after normalising - case, accents, spaces and punctuation ignored).
    Raises ValueError with a message fit to show."""
    question, answer = security_question.validate(question, answer)
    if len(security_question.normalize_answer(answer)) < MIN_SECURITY_ANSWER_LENGTH:
        raise UserError("err.answer_short", n=MIN_SECURITY_ANSWER_LENGTH)
    return question, answer


def set_security_question(session: Session, current_pin: str, question: str, answer: str) -> None:
    """An administrator sets (or replaces) the installation's security
    question. Needs their current PIN, so a walk-up can't swap it."""
    if session.role != "admin":
        raise localized_auth("err.question_admin_only")
    question, answer = validate_security_answer(question, answer)
    _confirm_current_pin(session, current_pin, admin=True)
    answer_hash = auth.hash_pin(security_question.normalize_answer(answer))

    def run(conn):
        live = _fetch_by_id(conn, session.account_id)
        if live is None or not live["is_active"] or not live["employee_active"] or live["role"] != "admin":
            raise SessionInvalidError()
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
        _SQ_HASH, normalized, len(normalized) >= MIN_SECURITY_ANSWER_LENGTH, badge_id, new_pin, terminal,
        "Reset by answering the security question", "err.sq_none",
        "err.sq_wrong",
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
        if row["role"] != "admin" or not row["is_active"] or not row["employee_active"]:
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
        raise localized_auth(none_message)
    if status == "locked":
        raise localized_auth("err.recovery_locked", when=local_datetime_text(extra))
    if status == "not_admin":
        raise localized_auth("err.not_active_admin")
    raise localized_auth(wrong_message)


def change_own_pin(session: Session, current_pin: str, new_pin: str) -> None:
    """Signed-in person changes their own PIN; the current one must match
    (checked with the sign-in lockout against the account as it is NOW)."""
    row = _confirm_current_pin(session, current_pin)
    set_pin(row["badge_id"], new_pin, by=session.actor)


def set_role(badge_id: str, role: str, by: Actor | None = None, new_pin: str | None = None) -> None:
    """Change an account's role. Promoting someone to administrator must
    come with `new_pin` in the same call: an administrator PIN is longer
    (auth.MIN_ADMIN_PIN_LENGTH) than a till PIN, and the old short PIN must
    not become an admin key. It must also differ from the current PIN.
    Raises ValueError (missing / weak / unchanged PIN), LastAdminError,
    SelfActionError (`by` changing their own role), SessionInvalidError
    (`by` is no longer an administrator)."""
    if role not in auth.ROLES:
        raise ValueError(f"Unknown role {role!r}")
    current = get(badge_id)
    promoting = role == "admin" and current.role != "admin"
    pin_hash = None
    if promoting:
        if not new_pin:
            raise UserError("err.promote_needs_pin", n=auth.MIN_ADMIN_PIN_LENGTH)
        _check_pin(new_pin, "admin")
        with connection_scope() as conn:
            old_hash = conn.execute("SELECT pin_hash FROM accounts WHERE id = ?", (current.id,)).fetchone()[0]
        if auth.verify_pin(new_pin, old_hash):
            raise UserError("err.promote_new_pin")
        pin_hash = auth.hash_pin(new_pin)

    def run(conn):
        same = _check_actor(conn, by, current.badge_id)
        row = _account_row(conn, current.badge_id)
        if row["role"] == role:
            return
        if same:
            raise SelfActionError("change the role of")
        if role == "admin" and pin_hash is None:  # became non-admin while we were checking
            raise UserError("err.promote_try_again")
        if row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        if pin_hash is not None:
            conn.execute("UPDATE accounts SET role = ?, pin_hash = ?, failed_attempts = 0, locked_until = NULL, "
                         "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (role, pin_hash, row["id"]))
        else:
            conn.execute("UPDATE accounts SET role = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                         (role, row["id"]))
        _log(conn, "account_changed", row["badge_id"],
             detail=f"role {auth.role_label(row['role'])} -> {auth.role_label(role)}"
                    + (" · new PIN set" if pin_hash is not None else "") + (f" · by {by.label}" if by else ""))

    _write(run)


def set_active(badge_id: str, active: bool, by: Actor | None = None) -> None:
    """Switch an account on or off. Raises LastAdminError,
    SelfActionError (`by` switching themselves off), SessionInvalidError."""
    def run(conn):
        row = _account_row(conn, badge_id)
        same = _check_actor(conn, by, row["badge_id"])
        if not active and same:
            raise SelfActionError("switch off")
        if not active and row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        conn.execute("UPDATE accounts SET is_active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
                     (int(bool(active)), row["id"]))
        _log(conn, "account_changed", row["badge_id"],
             detail=("switched on" if active else "switched off") + (f" · by {by.label}" if by else ""))

    _write(run)


def unlock(badge_id: str, by: Actor | None = None) -> None:
    def run(conn):
        row = _account_row(conn, badge_id)
        _check_actor(conn, by, row["badge_id"])
        conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = NULL WHERE id = ?", (row["id"],))
        _log(conn, "unlocked", row["badge_id"], detail=f"by {by.label}" if by else None)

    _write(run)


def delete(badge_id: str, by: Actor | None = None) -> None:
    """Remove the account (the employee stays in Workforce). Raises
    LastAdminError, SelfActionError (`by` removing their own account),
    SessionInvalidError."""
    def run(conn):
        row = _account_row(conn, badge_id)
        same = _check_actor(conn, by, row["badge_id"])
        if same:
            raise SelfActionError("remove")
        if row["role"] == "admin":
            guard_last_admin(conn, row["employee_id"])
        conn.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
        _log(conn, "account_changed", row["badge_id"], detail="account removed" + (f" · by {by.label}" if by else ""))

    _write(run)
