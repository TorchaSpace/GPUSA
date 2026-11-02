"""The activity feed (Admin bell and Overview)."""

import json

import pytest

pytest.importorskip("alembic")

from test_server_purchasing import second_admin_and_manager  # noqa: E402,F401
from test_server_writes import _schema, client, login, sql  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _events(client):
    sql("TRUNCATE activity_events RESTART IDENTITY")
    for kind, severity, source in (("sale", "info", "pos"), ("low", "notice", "depot"), ("out", "warning", "depot"),
                                   ("down", "critical", "system")):
        sql("INSERT INTO activity_events (kind, severity, source, location_kind, location_code, actor, data_json) "
            f"VALUES ('{kind}', '{severity}', '{source}', 'warehouse', 'WH-01', 'Ali', '{json.dumps({'n': 1})}')")


def test_latest_and_count(client):
    h = login(client)
    assert client.get("/activity/latest", headers=h).json() == {"id": 4}
    assert client.get("/activity/count", headers=h).json() == {"count": 3}  # notice and above
    assert client.get("/activity/count?after_id=2&min_severity=warning", headers=h).json() == {"count": 2}
    assert client.get("/activity/count?min_severity=info", headers=h).json() == {"count": 4}
    assert client.get("/activity/count?min_severity=loud", headers=h).status_code == 422


def test_since_oldest_first_and_recent_newest_first(client):
    h = login(client)
    rows = client.get("/activity/since?after_id=1&limit=2", headers=h).json()
    assert [r["id"] for r in rows] == [2, 3] and rows[0]["data"] == {"n": 1} and rows[0]["location_code"] == "WH-01"
    assert [r["kind"] for r in client.get("/activity/recent?limit=2", headers=h).json()] == ["down", "out"]
    assert [r["kind"] for r in client.get("/activity/recent?min_severity=warning", headers=h).json()] == ["down", "out"]
    assert [r["kind"] for r in client.get("/activity/recent?sources=pos&sources=system", headers=h).json()] == ["down", "sale"]


def test_admins_only(client):
    h = login(client)
    mgr, _ = second_admin_and_manager(client, h)
    assert client.get("/activity/latest", headers=mgr).status_code == 403
    assert client.get("/activity/recent", headers=mgr).status_code == 403
    assert client.get("/activity/latest").status_code in (401, 403)


def test_changes_counter_moves_with_writes(client):
    import time
    h = login(client)
    first = client.get("/changes", headers=h).json()["version"]
    sql("INSERT INTO activity_events (kind) VALUES ('x')")
    for _ in range(30):  # statistics are folded in about once a second
        time.sleep(0.3)
        now = client.get("/changes", headers=h).json()["version"]
        if now != first:
            break
    assert now > first
    assert client.get("/changes").status_code in (401, 403)
