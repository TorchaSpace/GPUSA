"""Who may write what, checked on every write.

A token is a snapshot from sign-in and may be hours old. Every write first
checks the token (role and area it was issued for), then re-reads the account
INSIDE the write's own transaction: an administrator who was demoted or
switched off on another machine cannot finish a write with an old token.
Same rule as the desktop apps (database/authz.py).
"""

from __future__ import annotations

from fastapi import HTTPException

from .auth_routes import ALLOWED_ROLES, Session


def require(session: Session, *, roles: frozenset[str], areas: frozenset[str]) -> None:
    """403 unless the token's role is in `roles` and it was issued for one of `areas`."""
    if session.role not in roles:
        raise HTTPException(status_code=403, detail=f"A {session.role} account cannot do this.")
    if session.area not in areas:
        raise HTTPException(status_code=403, detail=f"Sign in to {' or '.join(sorted(areas))} to do this.")


def require_live(conn, session: Session, *, roles: frozenset[str]) -> None:
    """401 unless the account is still switched on, its employee is active, and
    its CURRENT role is in `roles` and may still open the token's area.
    Locks the account row until the transaction ends. Needs a dict_row connection."""
    row = conn.execute(
        "SELECT a.role, a.is_active, e.is_active AS employee_active "
        "FROM accounts a JOIN employees e ON e.id = a.employee_id WHERE a.id = %s FOR SHARE OF a",
        (session.account_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Sign in again.")
    role = row["role"]
    if (not row["is_active"] or not row["employee_active"] or role not in roles
            or role not in ALLOWED_ROLES.get(session.area, ())):
        raise HTTPException(status_code=401, detail="Your account changed. Sign in again.")


def actor_label(session: Session) -> str:
    """Text snapshot stored on the rows a person touched (desktop format: "Name · BADGE")."""
    return f"{session.name} · {session.badge_id}"
