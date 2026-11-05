"""First-time set-up from the Admin screen: make the very first administrator without a terminal.

The door is open only while the server has NO active administrator account, and it closes the moment one
exists (checked and written in one transaction under a lock, so two people racing cannot both win). After
that, administrators are added from Admin > Workforce / Settings by an administrator who is signed in, or
with ``python -m gpusa_server.create_admin``. Same PIN rules as that command.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import db, security
from .auth_routes import _log as _auth_log
from .people_routes import pin_problem
from .stock_routes import _fail

router = APIRouter(prefix="/setup", tags=["setup"])

SETUP_LOCK = 7_340_001  # pg advisory lock key: one first-administrator set-up at a time
_ACTIVE_ADMINS = ("SELECT 1 FROM accounts a JOIN employees e ON e.id = a.employee_id "
                  "WHERE a.role = 'admin' AND a.is_active AND e.is_active LIMIT 1")


class FirstAdminIn(BaseModel):
    badge_id: str = Field(min_length=1, max_length=32)
    name: str = Field(default="", max_length=120)
    pin: str = Field(min_length=1, max_length=64)
    terminal: str = Field(default="Admin", max_length=64)


@router.get("/status")
def status(request: Request) -> dict:
    """Whether Admin should offer 'create the first administrator'. Anyone may ask; it says nothing else."""
    with db.connect(request.app.state.settings) as conn:
        return {"needs_first_admin": conn.execute(_ACTIVE_ADMINS).fetchone() is None}


@router.post("/first-admin", status_code=201)
def first_admin(body: FirstAdminIn, request: Request) -> dict:
    badge = security.normalize_badge_id(body.badge_id)
    name = " ".join((body.name or "").split())
    if not badge:
        raise _fail(422, "A badge is required.")
    if problem := pin_problem(body.pin, "admin"):
        raise _fail(422, problem)
    pin_hash = security.hash_pin(body.pin)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK,))
        if conn.execute(_ACTIVE_ADMINS).fetchone() is not None:
            raise _fail(409, "An administrator already exists - sign in instead.")
        employee = conn.execute("SELECT id FROM employees WHERE upper(badge_id) = %s", (badge,)).fetchone()
        if employee is None:
            if not name:
                raise _fail(422, "Your name is required.")
            employee = conn.execute(
                "INSERT INTO employees (badge_id, name, title, role, location_type, location_name) "
                "VALUES (%s, %s, 'Administrator', 'Management', 'Warehouse', 'HQ') RETURNING id",
                (badge, name)).fetchone()
        else:
            conn.execute("UPDATE employees SET is_active = TRUE WHERE id = %s", (employee["id"],))
        conn.execute(
            "INSERT INTO accounts (employee_id, role, pin_hash) VALUES (%s, 'admin', %s) "
            "ON CONFLICT (employee_id) DO UPDATE SET role = 'admin', pin_hash = EXCLUDED.pin_hash, "
            "is_active = TRUE, failed_attempts = 0, locked_until = NULL, updated_at = now()",
            (employee["id"], pin_hash))
        _auth_log(conn, "account_created", badge, "admin", body.terminal, "First administrator")
    return {"badge_id": badge}
