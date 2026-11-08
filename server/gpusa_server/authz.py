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


def require_root(session: Session) -> None:
    """403 unless the person is the root user, signed in to Admin. Account management, the sign-in log,
    recovery codes and the server address are root-only."""
    if not session.is_root:
        raise HTTPException(status_code=403, detail="Only the root user can do this.")
    if session.area != "admin":
        raise HTTPException(status_code=403, detail="Sign in to admin to do this.")


def require_live(conn, session: Session, *, roles: frozenset[str], root: bool = False) -> None:
    """401 unless the account is still switched on, its employee is active, and
    its CURRENT role is in `roles` and may still open the token's area.
    Locks the account row until the transaction ends. Needs a dict_row connection."""
    row = conn.execute(
        "SELECT a.role, a.is_root, a.is_active, e.is_active AS employee_active "
        "FROM accounts a JOIN employees e ON e.id = a.employee_id WHERE a.id = %s FOR SHARE OF a",
        (session.account_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Sign in again.")
    role = row["role"]
    if (not row["is_active"] or not row["employee_active"] or role not in roles
            or role not in ALLOWED_ROLES.get(session.area, ()) or (root and not row["is_root"])):
        raise HTTPException(status_code=401, detail="Your account changed. Sign in again.")


def actor_label(session: Session) -> str:
    """Text snapshot stored on the rows a person touched (desktop format: "Name · BADGE")."""
    return f"{session.name} · {session.badge_id}"


def own_shop(conn, session: Session) -> str | None:
    """Which dealership this person is limited to: None for roles that see every shop (administrators, depot
    managers); for a cashier, the code of the dealership their employee record is assigned to. A cashier whose
    record is not assigned to a dealership gets 403, so a till login can never reach another shop's sales,
    refunds, day count, shipments or stock. Needs a dict_row connection."""
    if session.role != "cashier":
        return None
    row = conn.execute(
        "SELECT d.code FROM employees e JOIN dealerships d ON e.location_type = 'Dealership' AND "
        "(upper(d.code) = upper(btrim(e.location_name)) OR lower(regexp_replace(btrim(d.name), '\\s+', ' ', 'g')) = "
        "lower(regexp_replace(btrim(e.location_name), '\\s+', ' ', 'g'))) "
        "WHERE upper(e.badge_id) = upper(%s) AND e.is_active LIMIT 1", (session.badge_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="Your badge is not assigned to a dealership. "
                                                    "Ask an administrator to set it in Workforce.")
    return row["code"]


def scope_shop(conn, session: Session, requested: str | None) -> str | None:
    """The shop a request may act on: what was asked for, unless the person is a cashier - then only their own
    (asking for another one is a 403; asking for none means their own)."""
    own = own_shop(conn, session)
    wanted = (requested or "").strip().upper() or None
    if own is None:
        return wanted
    if wanted is not None and wanted != own.upper():
        raise HTTPException(status_code=403, detail="This till belongs to another dealership.")
    return own


def same_shop(conn, session: Session, shop_code: str | None) -> bool:
    """Whether the person may see a record belonging to `shop_code` (everyone but a cashier always may)."""
    own = own_shop(conn, session)
    return own is None or (shop_code or "").upper() == own.upper()


def own_warehouse(conn, session: Session) -> str | None:
    """The warehouse a depot manager is limited to: the one their employee record is assigned to (matched by
    code or name). None for every other role. A depot manager whose record is not assigned to a warehouse gets
    403, so a depot console login can never act on another depot's stock, shipments, orders or ledger.
    Needs a dict_row connection."""
    if session.role != "depot_manager":
        return None
    row = conn.execute(
        "SELECT w.code FROM employees e JOIN warehouses w ON e.location_type = 'Warehouse' AND "
        "(upper(w.code) = upper(btrim(e.location_name)) OR lower(regexp_replace(btrim(w.name), '\\\\s+', ' ', 'g')) = "
        "lower(regexp_replace(btrim(e.location_name), '\\\\s+', ' ', 'g'))) "
        "WHERE upper(e.badge_id) = upper(%s) AND e.is_active LIMIT 1", (session.badge_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="Your badge is not assigned to a warehouse. "
                                                    "Ask an administrator to set it in Workforce.")
    return row["code"]


def scope_warehouse(conn, session: Session, code: str | None) -> None:
    """403 unless `code` (a warehouse code, or a site label like "WH-01 · Main") is the depot manager's own
    warehouse. Administrators may use any. An empty code is not anyone's, so a depot manager may not use it."""
    own = own_warehouse(conn, session)
    if own is None:
        return
    if (code or "").split("·")[0].strip().upper() != own.upper():
        raise HTTPException(status_code=403, detail="That belongs to another warehouse.")


def same_warehouse(conn, session: Session, code: str | None) -> bool:
    """Whether the person may see a record belonging to warehouse `code` (everyone but a depot manager may)."""
    own = own_warehouse(conn, session)
    return own is None or (code or "").split("·")[0].strip().upper() == own.upper()
