"""Server versions of the activity-feed reads (Admin's bell and Overview). The server writes the events inside
the transactions they describe; here we only read them."""

from __future__ import annotations

from urllib.parse import urlencode

from database import remote
from database.remote_stock import db_timestamp
from shared.models import ActivityEvent


def _event(data: dict) -> ActivityEvent:
    from database.transaction_repository import _as_local, _parse_timestamp  # same timestamp rules as sales

    return ActivityEvent(
        id=int(data["id"]), at=_as_local(_parse_timestamp(db_timestamp(data["at"]))), kind=data["kind"],
        severity=data["severity"], source=data["source"], location_kind=data.get("location_kind"),
        location_code=data.get("location_code"), location_name=data.get("location_name"), actor=data.get("actor"),
        data=data.get("data") if isinstance(data.get("data"), dict) else {})


def latest_id() -> int:
    return int(remote.call("GET", "/activity/latest")["id"])


def list_since(after_id: int, limit: int) -> list[ActivityEvent]:
    query = urlencode({"after_id": int(after_id), "limit": int(limit)})
    return [_event(d) for d in remote.call("GET", "/activity/since?" + query)]


def list_recent(limit: int, min_severity: str, sources: tuple[str, ...] | None) -> list[ActivityEvent]:
    query = urlencode({"limit": int(limit), "min_severity": min_severity,
                       **({"sources": list(sources)} if sources else {})}, doseq=True)
    return [_event(d) for d in remote.call("GET", "/activity/recent?" + query)]


def count_since(after_id: int, min_severity: str) -> int:
    query = urlencode({"after_id": int(after_id), "min_severity": min_severity})
    return int(remote.call("GET", "/activity/count?" + query)["count"])
