"""Live authorisation for sensitive repository writes - a leaf module (it
imports no other repository, so product/employee/ledger/... can all use it
without an import cycle).

A Session or Actor is a snapshot taken at sign-in and may be hours old. The
writes that matter re-check, INSIDE their own BEGIN IMMEDIATE transaction,
that the person still has an active account, on an active employee, whose
role may open the area TODAY - so an administrator who was demoted or
switched off on another machine cannot finish what the open window lets
them start. `actor=None` (setup scripts, tests) skips the check, as
everywhere else in the repositories.
"""

from __future__ import annotations

import sqlite3

from database.exceptions import SessionInvalidError
from shared import auth
from shared.auth import Actor


def require_live_role(conn: sqlite3.Connection, actor: Actor | None, area: str = auth.AREA_ADMIN) -> None:
    """SessionInvalidError unless `actor` (found by badge, any letter case)
    still has a switched-on account, an active employee record, and a role
    that may open `area`. Reads on the caller's connection."""
    if actor is None:
        return
    raw = (actor.badge_id or "").strip()
    row = conn.execute(
        "SELECT a.role, a.is_active, e.is_active AS employee_active "
        "FROM accounts a JOIN employees e ON e.id = a.employee_id "
        "WHERE e.badge_id = ? OR upper(e.badge_id) = ? ORDER BY (e.badge_id = ?) DESC, e.id LIMIT 1",
        (raw, raw.upper(), raw.upper()),
    ).fetchone()
    if row is None or not row["is_active"] or not row["employee_active"] or not auth.can_open(row["role"], area):
        raise SessionInvalidError()


def require_live_admin(conn: sqlite3.Connection, actor: Actor | None) -> None:
    """require_live_role for the Admin app."""
    require_live_role(conn, actor, auth.AREA_ADMIN)
