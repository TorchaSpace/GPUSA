"""Getting back into Admin when the administrator PIN is forgotten: a printed recovery code and a security
question. Same rules as the desktop's account_repository:

- Both are stored only as salted hashes in ``app_settings`` (``recovery.*`` keys, never served by /settings).
- The recovery code is shown once when it is made; a new one replaces the old.
- Wrong guesses are counted for BOTH methods together; five lock recovery for 15 minutes.
- Making either needs an administrator signed in; changing the question also needs their current PIN.
- Resetting works without signing in (that is the point), and only for an active administrator's account.
"""

from __future__ import annotations

import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from psycopg.rows import dict_row, tuple_row
from pydantic import BaseModel, Field

from . import authz, db, security
from .auth_routes import LOCK_MINUTES, MAX_FAILED_ATTEMPTS, Session, _log as _auth_log, current_session
from .people_routes import pin_problem
from .stock_routes import _fail

router = APIRouter(prefix="/recovery", tags=["recovery"])

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
CODE_LENGTH = 16
MIN_QUESTION, MAX_QUESTION, MIN_ANSWER = 8, 120, 4
CODE_HASH, FAILED, LOCKED = "recovery.code_hash", "recovery.failed", "recovery.locked_until"
ALL_FAILED = "recovery.failed_all"  # wrong guesses from anywhere; a high ceiling against many-address guessing
ALL_LIMIT = MAX_FAILED_ATTEMPTS * 10
QUESTION, ANSWER_HASH = "recovery.question", "recovery.answer_hash"
ADMINS = frozenset({"admin"})
ADMIN_AREA = frozenset({"admin"})


def _fold(text: str | None) -> str:
    text = (text or "").replace("İ", "i").replace("ı", "i")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch))


def normalize_answer(text: str | None) -> str:
    return "".join(ch for ch in _fold(text) if ch.isalnum())


def normalize_code(text: str | None) -> str:
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def _put(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE "
                 "SET value = EXCLUDED.value, updated_at = now()", (key, value))


def _values(conn, *keys: str) -> dict[str, str]:
    rows = conn.cursor(row_factory=tuple_row).execute("SELECT key, value FROM app_settings WHERE key = ANY(%s)",
                                                       (list(keys),)).fetchall()
    return {k: v for k, v in rows}


class StatusOut(BaseModel):
    has_code: bool
    question: str | None


@router.get("/status", response_model=StatusOut)
def status(request: Request) -> StatusOut:
    """Public: the sign-in screen shows the question and whether a code exists."""
    with db.connect(request.app.state.settings) as conn:
        values = _values(conn, CODE_HASH, QUESTION, ANSWER_HASH)
    return StatusOut(has_code=bool(values.get(CODE_HASH)),
                     question=values.get(QUESTION) if values.get(ANSWER_HASH) and values.get(QUESTION) else None)


def _live_admin(conn, session: Session) -> None:
    authz.require_live(conn, session, roles=ADMINS, root=True)


@router.post("/code")
def make_code(request: Request, session: Session = Depends(current_session)) -> dict:
    """A fresh recovery code - the only time it can be read. The old one stops working."""
    authz.require_root(session)
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    code = "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))
    code_hash = security.hash_pin(normalize_code(code))
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        _live_admin(conn, session)
        _put(conn, CODE_HASH, code_hash)
        _put(conn, FAILED, "0")
        _put(conn, LOCKED, "")
        _auth_log(conn, "recovery_code_created", session.badge_id, "admin", "", "New recovery code")
    return {"code": code}


class QuestionIn(BaseModel):
    current_pin: str = Field(max_length=64)
    question: str = Field(max_length=500)
    answer: str = Field(max_length=500)


@router.put("/question", status_code=204)
def set_question(body: QuestionIn, request: Request, session: Session = Depends(current_session)) -> None:
    authz.require_root(session)
    question = " ".join(body.question.split())
    if not MIN_QUESTION <= len(question) <= MAX_QUESTION:
        raise _fail(422, f"The question needs {MIN_QUESTION} to {MAX_QUESTION} characters.")
    answer = normalize_answer(body.answer)
    if len(answer) < MIN_ANSWER:
        raise _fail(422, f"The answer needs at least {MIN_ANSWER} letters or digits.")
    now = datetime.now(timezone.utc)
    error = None
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        _live_admin(conn, session)
        row = conn.execute("SELECT a.id, a.pin_hash, a.failed_attempts, a.locked_until FROM accounts a "
                           "WHERE a.id = %s FOR UPDATE", (session.account_id,)).fetchone()
        if row is None:
            raise _fail(401, "Sign in again.")
        if row["locked_until"] and row["locked_until"] > now:
            raise _fail(423, f"Account locked until {row['locked_until'].isoformat()}.")
        if not security.verify_pin(body.current_pin, row["pin_hash"]):
            attempts = row["failed_attempts"] + 1
            if attempts >= MAX_FAILED_ATTEMPTS:
                until = now + timedelta(minutes=LOCK_MINUTES)
                conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = %s WHERE id = %s", (until, row["id"]))
                _auth_log(conn, "locked", session.badge_id, "admin", "", f"{attempts} wrong PINs")
                error = _fail(423, f"Account locked until {until.isoformat()}.")
            else:
                conn.execute("UPDATE accounts SET failed_attempts = %s WHERE id = %s", (attempts, row["id"]))
                _auth_log(conn, "failed", session.badge_id, "admin", "", "wrong PIN (security question)")
                error = _fail(401, "The current PIN is wrong.")
        else:
            _put(conn, QUESTION, question)
            _put(conn, ANSWER_HASH, security.hash_pin(answer))
            _put(conn, FAILED, "0")
            _put(conn, LOCKED, "")
            _auth_log(conn, "security_question_set", session.badge_id, "admin", "", None)
    if error is not None:
        raise error


class ResetIn(BaseModel):
    method: Literal["code", "answer"]
    secret: str = Field(max_length=500)
    badge_id: str = Field(max_length=64)
    new_pin: str = Field(max_length=64)
    new_badge_id: str | None = Field(default=None, max_length=32)  # optional: also give the account a new badge
    terminal: str = Field(default="Admin", max_length=100)


@router.post("/reset")
def reset(body: ResetIn, request: Request) -> dict:
    """Set a new PIN (and, if asked, a new badge) for an administrator after the recovery code or the security
    answer. No sign-in. `badge_id` is the account's badge, or - for someone who forgot it - its exact name
    (shown only if exactly one active administrator has it). Nothing else about the account changes: the
    employee row keeps its id, so every sale, stock movement and record stays attached to it. Answers with
    the badge to sign in with."""
    if problem := pin_problem(body.new_pin, "admin"):
        raise _fail(422, problem)
    new_badge = security.normalize_badge_id(body.new_badge_id) if (body.new_badge_id or "").strip() else None
    hash_key = CODE_HASH if body.method == "code" else ANSWER_HASH
    secret = normalize_code(body.secret) if body.method == "code" else normalize_answer(body.secret)
    plausible = len(secret) == CODE_LENGTH if body.method == "code" else len(secret) >= MIN_ANSWER
    new_hash = security.hash_pin(body.new_pin)
    badge = security.normalize_badge_id(body.badge_id)
    now = datetime.now(timezone.utc)
    error = None
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('gpusa-recovery'))")  # one guess at a time
        # Wrong guesses are counted per address, so one PC mistyping (or attacking) cannot lock everyone else
        # out; a much higher ceiling over all addresses still stops guessing from many.
        who = (request.client.host if request.client else "?")
        f_key, l_key = f"{FAILED}:{who}", f"{LOCKED}:{who}"
        values = _values(conn, hash_key, f_key, l_key, ALL_FAILED, LOCKED)
        stored = values.get(hash_key)
        locked = max(values.get(l_key) or "", values.get(LOCKED) or "")
        locked_until = datetime.fromisoformat(locked) if locked else None
        if not stored:
            error = _fail(404, "No recovery code has been set up." if body.method == "code"
                          else "No security question has been set up.")
        elif locked_until and locked_until > now:
            error = _fail(423, f"Recovery is locked until {locked_until.isoformat()}.")
        else:
            ok = security.verify_pin(secret, stored) and plausible  # always hash, right or wrong
            if not ok:
                failed = int(values.get(f_key) or 0) + 1
                total = int(values.get(ALL_FAILED) or 0) + 1
                expiry = now + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_FAILED_ATTEMPTS else None
                _put(conn, f_key, "0" if expiry else str(failed))
                _put(conn, l_key, expiry.isoformat() if expiry else "")
                if total >= ALL_LIMIT:  # lock everyone for a while
                    _put(conn, LOCKED, (now + timedelta(minutes=LOCK_MINUTES)).isoformat())
                    total = 0
                _put(conn, ALL_FAILED, str(total))
                _auth_log(conn, "recovery_failed", badge or None, "admin", body.terminal,
                          f"Locked for {LOCK_MINUTES} minutes" if expiry else None)
                error = _fail(401, "That recovery code is wrong." if body.method == "code" else "That answer is wrong.")
            else:
                row, error = _find_admin(conn, badge, body.badge_id)
                if error is None and new_badge and new_badge != row["badge_id"].upper():
                    if len(new_badge) > 32:
                        error = _fail(422, "A badge can have at most 32 characters.")
                    elif conn.execute("SELECT 1 FROM employees WHERE upper(badge_id) = %s AND id <> %s",
                                      (new_badge, row["employee_id"])).fetchone():
                        error = _fail(409, f"The badge {new_badge} is already used by someone else.")
                if error is None:
                    conn.execute("UPDATE accounts SET pin_hash = %s, failed_attempts = 0, locked_until = NULL, "
                                 "updated_at = now() WHERE id = %s", (new_hash, row["id"]))
                    result_badge = row["badge_id"]
                    detail = ("Reset with the recovery code" if body.method == "code"
                              else "Reset by answering the security question")
                    if new_badge and new_badge != row["badge_id"].upper():
                        conn.execute("UPDATE employees SET badge_id = %s, updated_at = now() WHERE id = %s",
                                     (new_badge, row["employee_id"]))
                        detail += f"; badge changed from {row['badge_id']} to {new_badge}"
                        result_badge = new_badge
                    _put(conn, f_key, "0")
                    _put(conn, l_key, "")
                    _put(conn, ALL_FAILED, "0")
                    _put(conn, LOCKED, "")
                    _auth_log(conn, "pin_reset", result_badge, "admin", body.terminal, detail)
    if error is not None:
        raise error
    return {"badge_id": result_badge}


def _find_admin(conn, badge: str, typed: str):
    """(account row, None) for an active administrator named by badge or, failing that, by exact name."""
    cols = ("SELECT a.id, a.employee_id, e.badge_id FROM accounts a JOIN employees e ON e.id = a.employee_id "
            "WHERE a.role = 'admin' AND a.is_active AND e.is_active AND ")
    row = conn.execute(cols + "upper(e.badge_id) = %s FOR UPDATE OF a", (badge,)).fetchone()
    if row is not None:
        return row, None
    wanted = normalize_answer(typed)
    if wanted:
        rows = [r for r in conn.execute(cols.replace("e.badge_id FROM", "e.badge_id, e.name FROM") + "TRUE "
                                        "FOR UPDATE OF a").fetchall() if normalize_answer(r["name"]) == wanted]
        if len(rows) == 1:
            return rows[0], None
        if len(rows) > 1:
            return None, _fail(409, "More than one administrator has that name; type the badge instead.")
    return None, _fail(403, "That badge is not an active administrator.")
