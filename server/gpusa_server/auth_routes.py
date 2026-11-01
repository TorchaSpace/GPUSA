"""POST /auth/login and the ``current_session`` dependency.

Same rules as the desktop sign-in (database/account_repository.py): badge
matched in any letter case, a wrong PIN says nothing about whether the badge
exists, 5 wrong PINs in a row lock the account for 5 minutes, a disabled
account or a role that may not open the area is refused, and every outcome
lands in ``auth_events``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from . import db, security

MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 5
AREAS = ("admin", "depot_console", "pos")
ALLOWED_ROLES = {
    "admin": frozenset({"admin"}),
    "depot_console": frozenset({"admin", "depot_manager"}),
    "pos": frozenset({"admin", "cashier"}),
}

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    badge_id: str = Field(max_length=64)
    pin: str = Field(max_length=64)
    area: str
    terminal: str = Field(default="", max_length=128)


class LoginResponse(BaseModel):
    token: str
    badge_id: str
    name: str
    role: str
    area: str


@dataclass(frozen=True)
class Session:
    account_id: int
    badge_id: str
    name: str
    role: str
    area: str


def _log(conn, event: str, badge_id: str | None, area: str, terminal: str, detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO auth_events (badge_id, event, area, terminal, detail) VALUES (%s, %s, %s, %s, %s)",
        (badge_id or None, event, area, terminal or None, detail),
    )


def _fail(status: int, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail=message)


WRONG = "Badge or PIN is wrong."


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    settings = request.app.state.settings
    if not settings.jwt_secret or settings.mode == "none":
        raise _fail(503, "Sign-in is not configured on this server.")
    if body.area not in AREAS:
        raise _fail(422, f"area must be one of {AREAS}")

    badge = security.normalize_badge_id(body.badge_id)
    now = datetime.now(timezone.utc)
    error: HTTPException | None = None
    token: LoginResponse | None = None

    # One transaction: the failure counters must be saved even when we answer 401.
    with db.connect(settings) as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT a.id, a.role, a.pin_hash, a.is_active, a.locked_until, a.failed_attempts, "
            "e.badge_id, e.name, e.is_active AS employee_active "
            "FROM accounts a JOIN employees e ON e.id = a.employee_id "
            "WHERE upper(e.badge_id) = %s FOR UPDATE OF a",
            (badge,),
        ).fetchone()
        if row is None:
            security.verify_pin(body.pin, security.dummy_hash())
            _log(conn, "failed", badge, body.area, body.terminal, "unknown badge")
            error = _fail(401, WRONG)
        elif row["locked_until"] and row["locked_until"] > now:
            _log(conn, "failed", row["badge_id"], body.area, body.terminal, "locked")
            error = _fail(423, f"Account locked until {row['locked_until'].isoformat()}.")
        elif not security.verify_pin(body.pin, row["pin_hash"]):
            attempts = row["failed_attempts"] + 1
            if attempts >= MAX_FAILED_ATTEMPTS:
                until = now + timedelta(minutes=LOCK_MINUTES)
                conn.execute("UPDATE accounts SET failed_attempts = 0, locked_until = %s WHERE id = %s",
                             (until, row["id"]))
                _log(conn, "locked", row["badge_id"], body.area, body.terminal, f"{attempts} wrong PINs")
                error = _fail(423, f"Account locked until {until.isoformat()}.")
            else:
                conn.execute("UPDATE accounts SET failed_attempts = %s WHERE id = %s", (attempts, row["id"]))
                _log(conn, "failed", row["badge_id"], body.area, body.terminal, "wrong PIN")
                error = _fail(401, WRONG)
        elif not row["is_active"] or not row["employee_active"]:
            _log(conn, "refused", row["badge_id"], body.area, body.terminal, "account switched off")
            error = _fail(403, "This account is switched off.")
        elif row["role"] not in ALLOWED_ROLES[body.area]:
            _log(conn, "refused", row["badge_id"], body.area, body.terminal, f"{row['role']} role")
            error = _fail(403, f"A {row['role']} account cannot open {body.area}.")
        else:
            new_hash = security.hash_pin(body.pin) if security.needs_rehash(row["pin_hash"]) else row["pin_hash"]
            conn.execute(
                "UPDATE accounts SET failed_attempts = 0, locked_until = NULL, last_sign_in_at = %s, "
                "pin_hash = %s, updated_at = %s WHERE id = %s",
                (now, new_hash, now, row["id"]),
            )
            _log(conn, "sign_in", row["badge_id"], body.area, body.terminal)
            token = LoginResponse(
                token=security.make_token(settings.jwt_secret, account_id=row["id"], badge_id=row["badge_id"],
                                          name=row["name"], role=row["role"], area=body.area, now=now),
                badge_id=row["badge_id"], name=row["name"], role=row["role"], area=body.area,
            )
    if error is not None:
        raise error
    assert token is not None
    return token


def current_session(request: Request,
                    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Session:
    settings = request.app.state.settings
    if credentials is None or not settings.jwt_secret:
        raise _fail(401, "Sign in first.")
    try:
        claims = security.read_token(settings.jwt_secret, credentials.credentials)
        return Session(account_id=int(claims["sub"]), badge_id=claims["badge"], name=claims["name"],
                       role=claims["role"], area=claims["area"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise _fail(401, "Sign in again.") from None


@router.get("/me")
def me(session: Session = Depends(current_session)) -> dict:
    return {"badge_id": session.badge_id, "name": session.name, "role": session.role, "area": session.area}
