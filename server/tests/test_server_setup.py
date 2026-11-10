"""Root account from the Admin screen: open only while the server has no administrator."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import PIN, _schema, client, login, sql  # noqa: E402,F401


def first(client, badge="b-100", name=" Zed  Stone ", pin="753159", **extra):
    return client.post("/setup/first-admin", json={"badge_id": badge, "name": name, "pin": pin, **extra})


def test_set_up_is_open_only_while_there_is_no_administrator(client):
    # the fixture already made B-001: the door is shut
    assert client.get("/setup/status").json() == {"needs_first_admin": False, "needs_setup_code": False}
    refused = first(client)
    assert refused.status_code == 409 and "already exists" in refused.json()["detail"]
    assert sql("SELECT count(*) FROM employees WHERE badge_id = 'B-100'") == [(0,)]

    sql("DELETE FROM accounts")  # a fresh server
    assert client.get("/setup/status").json() == {"needs_first_admin": True, "needs_setup_code": False}
    ok = first(client, terminal="Counter")
    assert ok.status_code == 201 and ok.json() == {"badge_id": "B-100"}
    assert sql("SELECT e.name, a.role, a.is_active FROM employees e JOIN accounts a ON a.employee_id = e.id "
               "WHERE e.badge_id = 'B-100'") == [("Zed Stone", "admin", True)]
    assert sql("SELECT event, area, terminal, detail FROM auth_events WHERE badge_id = 'B-100'") == [
        ("account_created", "admin", "Counter", "Root account")]
    assert client.get("/setup/status").json() == {"needs_first_admin": False, "needs_setup_code": False}
    r = client.post("/auth/login", json={"badge_id": "B-100", "pin": "753159", "area": "admin"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert first(client, badge="B-200").status_code == 409   # the door closed behind the first one


def test_set_up_checks_the_input_and_reuses_a_known_badge(client):
    sql("DELETE FROM accounts")
    assert first(client, pin="1234").status_code == 422        # administrator PINs are 6+ digits, no runs
    assert first(client, pin="123456").status_code == 422
    assert first(client, name="  ").status_code == 422          # a new badge needs a name
    assert first(client, badge="  ").status_code == 422
    assert sql("SELECT count(*) FROM employees WHERE badge_id = 'B-100'") == [(0,)]
    # a badge that already exists as an employee becomes the administrator without retyping the name
    assert first(client, badge="b-001", name="").status_code == 201
    assert sql("SELECT count(*) FROM employees WHERE badge_id = 'B-001'") == [(1,)]
    assert sql("SELECT role FROM accounts") == [("admin",)]


def test_a_switched_off_administrator_does_not_count(client):
    sql("UPDATE accounts SET is_active = FALSE")
    assert client.get("/setup/status").json() == {"needs_first_admin": True, "needs_setup_code": False}
    assert first(client, badge="B-001", name="").status_code == 201
    assert sql("SELECT is_active FROM accounts") == [(True,)]


def test_a_setup_code_in_the_environment_is_required_for_the_first_administrator(client, monkeypatch):
    sql("DELETE FROM accounts")
    monkeypatch.setenv("GPUSA_SETUP_CODE", "K7QD2MXH")
    assert client.get("/setup/status").json() == {"needs_first_admin": True, "needs_setup_code": True}
    assert first(client).status_code == 403
    assert first(client, setup_code="wrong").status_code == 403
    assert sql("SELECT count(*) FROM accounts") == [(0,)]
    assert first(client, setup_code=" k7qd2mxh ").status_code == 201   # case and spaces don't matter


def test_without_a_setup_code_only_the_server_pc_creates_root(client, monkeypatch):
    """A fresh server with no setup code must not hand root to whoever reaches its port first."""
    from fastapi.testclient import TestClient

    sql("DELETE FROM accounts")
    monkeypatch.delenv("GPUSA_SETUP_CODE", raising=False)
    elsewhere = TestClient(client.app, client=("192.168.1.50", 50000))
    assert elsewhere.get("/setup/status").json() == {"needs_first_admin": True, "needs_setup_code": True}
    refused = first(elsewhere)
    assert refused.status_code == 403 and "GPUSA_SETUP_CODE" in refused.json()["detail"]
    assert first(elsewhere, setup_code="anything").status_code == 403
    # a proxy on the server PC makes everyone look local: a forwarded request is not "this PC"
    proxied = client.post("/setup/first-admin", headers={"X-Forwarded-For": "203.0.113.9"},
                          json={"badge_id": "B-100", "name": "Zed", "pin": "753159"})
    assert proxied.status_code == 403
    assert sql("SELECT count(*) FROM accounts") == [(0,)]
    assert first(client).status_code == 201  # on the server PC itself
    # with a code, the other PC gets in by typing it
    sql("DELETE FROM accounts")
    monkeypatch.setenv("GPUSA_SETUP_CODE", "K7QD2MXH")
    assert first(elsewhere, badge="B-200", setup_code="k7qd2mxh").status_code == 201


def test_the_server_proves_itself_to_a_paired_computer(client):
    """Only a server holding the secret can answer the question a paired computer asks before it sends a PIN."""
    import hashlib
    import hmac

    device, nonce = "ab" * 16, "cd" * 16
    assert client.get("/auth/pairing", params={"device": device}).status_code == 401  # signed in only
    key = client.get("/auth/pairing", params={"device": device}, headers=login(client)).json()["key"]
    proof = client.get("/identity", params={"device": device, "nonce": nonce}).json()["proof"]
    assert proof == hmac.new(bytes.fromhex(key), bytes.fromhex(nonce), hashlib.sha256).hexdigest()
    other = client.get("/auth/pairing", params={"device": "ef" * 16}, headers=login(client)).json()["key"]
    assert other != key  # each computer has its own key
    for bad in ({"device": "x", "nonce": nonce}, {"device": device, "nonce": "zz" * 16}, {"device": device}):
        assert client.get("/identity", params=bad).status_code == 422
