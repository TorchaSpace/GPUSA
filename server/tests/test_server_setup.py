"""First administrator from the Admin screen: open only while the server has no administrator."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import PIN, _schema, client, login, sql  # noqa: E402,F401


def first(client, badge="b-100", name=" Zed  Stone ", pin="753159", **extra):
    return client.post("/setup/first-admin", json={"badge_id": badge, "name": name, "pin": pin, **extra})


def test_set_up_is_open_only_while_there_is_no_administrator(client):
    # the fixture already made B-001: the door is shut
    assert client.get("/setup/status").json() == {"needs_first_admin": False}
    refused = first(client)
    assert refused.status_code == 409 and "already exists" in refused.json()["detail"]
    assert sql("SELECT count(*) FROM employees WHERE badge_id = 'B-100'") == [(0,)]

    sql("DELETE FROM accounts")  # a fresh server
    assert client.get("/setup/status").json() == {"needs_first_admin": True}
    ok = first(client, terminal="Counter")
    assert ok.status_code == 201 and ok.json() == {"badge_id": "B-100"}
    assert sql("SELECT e.name, a.role, a.is_active FROM employees e JOIN accounts a ON a.employee_id = e.id "
               "WHERE e.badge_id = 'B-100'") == [("Zed Stone", "admin", True)]
    assert sql("SELECT event, area, terminal, detail FROM auth_events WHERE badge_id = 'B-100'") == [
        ("account_created", "admin", "Counter", "First administrator")]
    assert client.get("/setup/status").json() == {"needs_first_admin": False}
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
    assert client.get("/setup/status").json() == {"needs_first_admin": True}
    assert first(client, badge="B-001", name="").status_code == 201
    assert sql("SELECT is_active FROM accounts") == [(True,)]
