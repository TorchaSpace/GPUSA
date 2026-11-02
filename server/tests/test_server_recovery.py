"""Recovery code and security question."""

import pytest

pytest.importorskip("alembic")

from test_server_purchasing import second_admin_and_manager  # noqa: E402,F401
from test_server_writes import PIN, _schema, client, login, sql  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean(client):
    sql("TRUNCATE app_settings, auth_events RESTART IDENTITY")


def reset(client, method, secret, pin="753159", badge="b-001"):
    return client.post("/recovery/reset", json={"method": method, "secret": secret, "badge_id": badge, "new_pin": pin})


def sign_in(client, pin):
    return client.post("/auth/login", json={"badge_id": "B-001", "pin": pin, "area": "admin"}).status_code


def test_status_is_public_and_empty_at_first(client):
    assert client.get("/recovery/status").json() == {"has_code": False, "question": None}


def test_code_is_shown_once_and_resets_the_pin(client):
    h = login(client)
    code = client.post("/recovery/code", headers=h).json()["code"]
    assert len(code) == 19 and code.count("-") == 3
    assert client.get("/recovery/status").json()["has_code"] is True
    assert reset(client, "code", code.lower().replace("-", " "), "123").status_code == 422  # a weak PIN is refused
    assert reset(client, "code", code).status_code == 204
    assert sign_in(client, "753159") == 200 and sign_in(client, PIN) == 401
    assert sql("SELECT event FROM auth_events WHERE event IN ('recovery_code_created', 'pin_reset') ORDER BY id") == [
        ("recovery_code_created",), ("pin_reset",)]
    token = client.post("/auth/login", json={"badge_id": "B-001", "pin": "753159", "area": "admin"}).json()["token"]
    new = client.post("/recovery/code", headers={"Authorization": f"Bearer {token}"}).json()["code"]
    assert new != code and reset(client, "code", code).status_code == 401  # the old code stopped working


def test_wrong_guesses_lock_recovery_for_both_methods(client):
    h = login(client)
    client.post("/recovery/code", headers=h)
    client.put("/recovery/question", headers=h, json={"current_pin": PIN, "question": "Name of my first pet?",
                                                      "answer": "Fıstık"})
    for _ in range(4):
        assert reset(client, "code", "AAAA-AAAA-AAAA-AAAA").status_code == 401
    assert reset(client, "answer", "nope!").status_code == 401  # the fifth wrong guess locks it
    locked = reset(client, "answer", "fistik")
    assert locked.status_code == 423 and "locked" in locked.json()["detail"]
    sql("UPDATE app_settings SET value = '' WHERE key = 'recovery.locked_until'")
    assert reset(client, "answer", " FISTIK ").status_code == 204  # case, accents and spaces don't matter


def test_question_needs_the_current_pin_and_valid_text(client):
    h = login(client)
    body = {"current_pin": PIN, "question": "Name of my first pet?", "answer": "Rex1"}
    assert client.put("/recovery/question", headers=h, json={**body, "current_pin": "000000"}).status_code == 401
    assert client.put("/recovery/question", headers=h, json={**body, "question": "short"}).status_code == 422
    assert client.put("/recovery/question", headers=h, json={**body, "answer": "ab"}).status_code == 422
    assert client.put("/recovery/question", headers=h, json=body).status_code == 204
    assert client.get("/recovery/status").json()["question"] == "Name of my first pet?"
    assert "Rex1" not in str(sql("SELECT value FROM app_settings"))  # only a hash is kept


def test_nothing_set_up_and_non_admin_targets(client):
    assert reset(client, "code", "AAAA-AAAA-AAAA-AAAA").status_code == 404
    assert reset(client, "answer", "whatever").status_code == 404
    h = login(client)
    code = client.post("/recovery/code", headers=h).json()["code"]
    second_admin_and_manager(client, h)
    refused = reset(client, "code", code, badge="M-1")
    assert refused.status_code == 403
    assert reset(client, "code", code, badge="nobody").status_code == 403
    assert reset(client, "code", code).status_code == 204


def test_only_signed_in_administrators_make_secrets(client):
    h = login(client)
    mgr, _ = second_admin_and_manager(client, h)
    assert client.post("/recovery/code", headers=mgr).status_code == 403
    assert client.put("/recovery/question", headers=mgr, json={
        "current_pin": "2468", "question": "Name of my first pet?", "answer": "Rex1"}).status_code == 403
    assert client.post("/recovery/code").status_code in (401, 403)
    assert client.get("/settings").json() == {}  # recovery keys are never served
