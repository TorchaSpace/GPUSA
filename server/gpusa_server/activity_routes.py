"""The live activity feed (Admin's bell and Overview): what the tills and depots just did.

Events are written inside the transactions they describe (``activity.record``); this only reads them.
Administrators only. ``after_id`` pages forward (oldest first), ``/activity/recent`` is newest first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from . import authz, db
from .auth_routes import Session, current_session

router = APIRouter(prefix="/activity", tags=["activity"])

SEVERITIES = ("info", "notice", "warning", "critical")
Severity = Literal["info", "notice", "warning", "critical"]
Source = Literal["pos", "depot", "admin", "system"]


class EventOut(BaseModel):
    id: int
    at: datetime
    kind: str
    severity: str
    source: str
    location_kind: str | None
    location_code: str | None
    location_name: str | None
    actor: str | None
    data: dict


def _admin(session: Session) -> None:
    authz.require(session, roles=frozenset({"admin", "depot_manager", "cashier"}), areas=frozenset({"admin"}))


def _at_least(severity: str) -> list[str]:
    return list(SEVERITIES[SEVERITIES.index(severity):])


def _out(row: dict) -> EventOut:
    return EventOut(**{k: row[k] for k in EventOut.model_fields if k != "data"}, data=row["data_json"] or {})


changes_router = APIRouter(tags=["activity"])


@changes_router.get("/changes")
def changes(request: Request, _: Session = Depends(current_session)) -> dict:
    """A number that moves whenever data changes (rows written anywhere except sign-in bookkeeping). The
    windows poll it to refresh what is on screen, like SQLite's data_version did on one PC. Postgres folds
    row counts into its statistics about once a second, so a change shows up within a second or two."""
    with db.connect(request.app.state.settings) as conn:
        version = conn.execute(
            "SELECT COALESCE(SUM(n_tup_ins + n_tup_upd + n_tup_del), 0)::bigint FROM pg_stat_user_tables "
            "WHERE relname <> ALL(%s)", (["auth_events", "alembic_version"],)).fetchone()[0]
    return {"version": int(version)}


@router.get("/latest")
def latest(request: Request, session: Session = Depends(current_session)) -> dict:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        return {"id": int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM activity_events").fetchone()[0])}


@router.get("/count")
def count(request: Request, after_id: int = Query(default=0, ge=0), min_severity: Severity = "notice",
          session: Session = Depends(current_session)) -> dict:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        n = conn.execute("SELECT COUNT(*) FROM activity_events WHERE id > %s AND severity = ANY(%s)",
                         (after_id, _at_least(min_severity))).fetchone()[0]
    return {"count": int(n)}


@router.get("/since", response_model=list[EventOut])
def since(request: Request, after_id: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=0, le=1000),
          session: Session = Depends(current_session)) -> list[EventOut]:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute("SELECT * FROM activity_events WHERE id > %s ORDER BY id LIMIT %s",
                            (after_id, limit)).fetchall()
    return [_out(r) for r in rows]


@router.get("/recent", response_model=list[EventOut])
def recent(request: Request, limit: int = Query(default=50, ge=0, le=1000), min_severity: Severity = "info",
           sources: list[Source] | None = Query(default=None), session: Session = Depends(current_session)) -> list[EventOut]:
    _admin(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        rows = conn.execute(
            "SELECT * FROM activity_events WHERE severity = ANY(%s) AND (%s::text[] IS NULL OR source = ANY(%s)) "
            "ORDER BY id DESC LIMIT %s", (_at_least(min_severity), sources, sources, limit)).fetchall()
    return [_out(r) for r in rows]
