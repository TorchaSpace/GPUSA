"""Employees, badge attendance and sign-in accounts."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("alembic")

from test_server_writes import PIN, _schema, client, login, sql  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean_tables(client):
    sql("TRUNCATE attendance_records RESTART IDENTITY CASCADE")


def person(badge="E-100", **extra):
    return {"badge_id": badge, "name": " Mia  Stone ", "title": " Picker ", "role": "Operations",
            "location_type": "Warehouse", "location_name": "main", **extra}


def make(client, h, badge="E-100", **extra):
    r = client.post("/employees", headers=h, json=person(badge, **extra))
    assert r.status_code == 201, r.text
    return r.json()


def till_login(client, badge, pin):
    return client.post("/auth/login", json={"badge_id": badge, "pin": pin, "area": "pos"})


# --- employees ------------------------------------------------------------------------

def test_create_cleans_the_fields_and_refuses_a_twin(client):
    h = login(client)
    got = make(client, h, " e-100 ")
    assert (got["badge_id"], got["name"], got["title"], got["location_name"]) == ("E-100", "Mia Stone", "Picker", "main")
    assert client.post("/employees", headers=h, json=person("e-100")).status_code == 409
    assert [e["badge_id"] for e in client.get("/employees", headers=h).json()] == ["B-001", "E-100"]
    assert client.get("/employees/e-100", headers=h).json()["name"] == "Mia Stone"


@pytest.mark.parametrize("bad", [{"role": "Boss"}, {"location_type": "Moon"}, {"name": "  "}, {"badge_id": " "},
                                 {"location_name": "Nowhere"}, {"badge_id": "X" * 33}])
def test_create_validates(client, bad):
    r = client.post("/employees", headers=login(client), json={**person(), **bad})
    assert r.status_code == 422, r.text


def test_only_an_admin_manages_employees(client):
    h = login(client)
    make(client, h)
    sql("UPDATE accounts SET role = 'cashier'")
    pos = login(client, "pos")
    assert client.post("/employees", headers=pos, json=person("E-9")).status_code == 403
    assert client.get("/employees", headers=pos).status_code == 403  # the list is for admins and depot managers


def test_update_and_switching_off_closes_the_shift(client):
    h = login(client)
    make(client, h)
    assert client.post("/attendance/check-in", headers=h, json={"badge_id": "e-100"}).status_code == 200
    r = client.patch("/employees/E-100", headers=h, json={**person(), "name": "Mia S.", "is_active": False})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["is_active"]) == ("Mia S.", False)
    assert sql("SELECT check_out_at IS NOT NULL, note FROM attendance_records") == [
        (True, "Auto check-out: employee switched off")]
    assert client.post("/attendance/check-in", headers=h, json={"badge_id": "E-100"}).status_code == 409


def test_cannot_switch_off_or_delete_yourself_or_the_last_admin(client):
    h = login(client)
    me = {**person("B-001"), "name": "Ali", "location_name": "Main"}
    assert client.patch("/employees/B-001", headers=h, json={**me, "is_active": False}).status_code == 409
    assert client.delete("/employees/B-001", headers=h).status_code == 409


def test_delete_needs_no_history_and_takes_the_account_with_it(client):
    h = login(client)
    make(client, h, "E-1")
    make(client, h, "E-2")
    client.post("/attendance/check-in", headers=h, json={"badge_id": "E-2"})
    client.post("/accounts", headers=h, json={"badge_id": "E-1", "role": "cashier", "pin": "2468"})
    assert client.delete("/employees/E-2", headers=h).status_code == 409  # has attendance history
    assert client.delete("/employees/E-1", headers=h).status_code == 204
    assert sql("SELECT count(*) FROM accounts") == [(1,)]
    assert sql("SELECT action, kind, target FROM admin_audit") == [("deleted", "employee", "E-1")]
    assert client.delete("/employees/E-1", headers=h).status_code == 404


def test_locations_lists_names_and_codes(client):
    places = client.get("/employees/locations", headers=login(client)).json()
    assert [(p["name"], p["code"]) for p in places["Warehouse"]] == [("Main", "WH-01"), ("Spare", "WH-02")]
    assert [(p["name"], p["code"]) for p in places["Dealership"]] == [("Coast", "CST-04")]


# --- attendance -----------------------------------------------------------------------

def test_check_in_out_and_open_list(client):
    h = login(client)
    make(client, h)
    assert client.post("/attendance/check-in", headers=h, json={"badge_id": "e-100", "note": "early"}).status_code == 200
    assert client.post("/attendance/check-in", headers=h, json={"badge_id": "E-100"}).status_code == 409
    assert [o["badge_id"] for o in client.get("/attendance/open", headers=h).json()] == ["E-100"]
    assert client.post("/attendance/check-out", headers=h, json={"badge_id": "E-100", "note": "done"}).status_code == 200
    assert client.get("/attendance/open", headers=h).json() == []
    assert sql("SELECT note FROM attendance_records") == [("done",)]
    assert client.post("/attendance/check-out", headers=h, json={"badge_id": "E-100"}).status_code == 409
    assert client.post("/attendance/check-in", headers=h, json={"badge_id": "NOPE"}).status_code == 404


def test_depot_punches_are_scoped_to_the_warehouse(client):
    h = login(client)
    make(client, h, "E-1", location_name="Main")
    make(client, h, "E-2", location_name="Spare")
    make(client, h, "E-3", location_type="Dealership", location_name="Coast")
    punch = lambda badge: client.post("/attendance/check-in", headers=h, json={"badge_id": badge, "warehouse_code": "wh-01"})
    assert punch("E-1").status_code == 200
    assert punch("E-2").status_code == 409
    assert punch("E-3").status_code == 409
    ok = client.get("/attendance/operator", headers=h, params={"badge_id": "e-1", "warehouse_code": "WH-01"})
    assert ok.json() == {"badge_id": "E-1", "name": "Mia Stone"}
    assert client.get("/attendance/operator", headers=h, params={"badge_id": "E-2", "warehouse_code": "WH-02"}).status_code == 409  # not in


def test_a_till_cannot_punch(client):
    h = login(client)
    make(client, h)
    sql("UPDATE accounts SET role = 'cashier'")
    assert client.post("/attendance/check-in", headers=login(client, "pos"), json={"badge_id": "E-100"}).status_code == 403


def test_records_in_a_window(client):
    h = login(client)
    make(client, h)
    client.post("/attendance/check-in", headers=h, json={"badge_id": "E-100"})
    now = datetime.now(timezone.utc)
    day = {"start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=1)).isoformat()}
    rows = client.get("/attendance/records", headers=h, params=day).json()
    assert [(r["badge_id"], r["check_out_at"]) for r in rows] == [("E-100", None)]
    past = {"start": (now - timedelta(days=3)).isoformat(), "end": (now - timedelta(days=2)).isoformat()}
    assert client.get("/attendance/records", headers=h, params=past).json() == []


# --- accounts -------------------------------------------------------------------------

def test_create_account_and_sign_in_with_it(client):
    h = login(client)
    make(client, h)
    assert [e["badge_id"] for e in client.get("/accounts/without", headers=h).json()] == ["E-100"]
    r = client.post("/accounts", headers=h, json={"badge_id": "e-100", "role": "cashier", "pin": "2468"})
    assert r.status_code == 201, r.text
    assert (r.json()["badge_id"], r.json()["role"], r.json()["is_active"]) == ("E-100", "cashier", True)
    assert client.get("/accounts/without", headers=h).json() == []
    assert till_login(client, "E-100", "2468").status_code == 200
    assert client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "2468"}).status_code == 409


@pytest.mark.parametrize("pin,role", [("12", "cashier"), ("1111", "cashier"), ("1234", "cashier"), ("12ab", "cashier"),
                                      ("2468", "admin"), ("1" * 13, "cashier")])
def test_weak_pins_are_refused(client, pin, role):
    h = login(client)
    make(client, h)
    assert client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": role, "pin": pin}).status_code == 422


def test_admin_sets_someone_elses_pin_and_clears_a_lock(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "2468"})
    for _ in range(5):
        till_login(client, "E-100", "9999")
    assert till_login(client, "E-100", "2468").status_code == 423
    assert client.post("/accounts/E-100/unlock", headers=h).status_code == 204
    assert till_login(client, "E-100", "2468").status_code == 200
    assert client.post("/accounts/E-100/pin", headers=h, json={"pin": "7391"}).status_code == 204
    assert till_login(client, "E-100", "2468").status_code == 401
    assert till_login(client, "E-100", "7391").status_code == 200
    assert client.post("/accounts/B-001/pin", headers=h, json={"pin": "739104"}).status_code == 409  # not your own


def test_change_own_pin_needs_the_current_one(client):
    h = login(client)
    wrong = client.post("/accounts/me/pin", headers=h, json={"current_pin": "000999", "new_pin": "739104"})
    assert wrong.status_code == 401
    assert client.post("/accounts/me/pin", headers=h, json={"current_pin": PIN, "new_pin": "12"}).status_code == 422
    assert client.post("/accounts/me/pin", headers=h, json={"current_pin": PIN, "new_pin": "739104"}).status_code == 204
    assert client.post("/auth/login", json={"badge_id": "B-001", "pin": PIN, "area": "admin"}).status_code == 401
    assert client.post("/auth/login", json={"badge_id": "B-001", "pin": "739104", "area": "admin"}).status_code == 200


def test_change_own_pin_wrong_five_times_locks(client):
    h = login(client)
    for _ in range(5):
        client.post("/accounts/me/pin", headers=h, json={"current_pin": "000999", "new_pin": "739104"})
    assert client.post("/accounts/me/pin", headers=h, json={"current_pin": PIN, "new_pin": "739104"}).status_code == 423


def test_role_changes_and_promotion_needs_a_new_pin(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "2468"})
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin"}).status_code == 422
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin", "new_pin": "2468"}).status_code == 422
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin", "new_pin": "135792"}).status_code == 204
    assert client.post("/auth/login", json={"badge_id": "E-100", "pin": "135792", "area": "admin"}).status_code == 200
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "depot_manager"}).status_code == 204
    assert client.post("/accounts/B-001/role", headers=h, json={"role": "cashier"}).status_code == 409  # not your own
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "wizard"}).status_code == 422


def test_the_last_admin_is_protected(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "admin", "pin": "135792"})
    second = {"Authorization": "Bearer " + client.post(
        "/auth/login", json={"badge_id": "E-100", "pin": "135792", "area": "admin"}).json()["token"]}
    assert client.post("/accounts/B-001/active", headers=second, json={"active": False}).status_code == 204
    # E-100 is now the only active administrator
    assert client.post("/accounts/E-100/active", headers=second, json={"active": False}).status_code == 409
    assert client.delete("/accounts/E-100", headers=second).status_code == 409
    assert client.post("/accounts/B-001/active", headers=second, json={"active": True}).status_code == 204
    assert client.post("/accounts/B-001/role", headers=second, json={"role": "cashier"}).status_code == 204


def test_switch_off_and_delete_an_account(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "2468"})
    assert client.post("/accounts/E-100/active", headers=h, json={"active": False}).status_code == 204
    assert till_login(client, "E-100", "2468").status_code == 403
    assert client.delete("/accounts/E-100", headers=h).status_code == 204
    assert client.delete("/accounts/E-100", headers=h).status_code == 404
    assert client.delete("/accounts/B-001", headers=h).status_code == 409
    assert client.post("/accounts/B-001/active", headers=h, json={"active": False}).status_code == 409


def test_account_changes_are_logged(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "2468"})
    client.post("/accounts/E-100/pin", headers=h, json={"pin": "7391"})
    events = client.get("/accounts/events", headers=h).json()
    assert [e["event"] for e in events if e["badge_id"] == "E-100"] == ["pin_changed", "account_created"]
    assert client.get("/accounts", headers=h).json()[0]["name"] == "Ali"
    sql("UPDATE accounts SET role = 'cashier'")
    assert client.get("/accounts", headers=login(client, "pos")).status_code == 403


def test_floor_movement_is_credited_to_the_operator_badge(client):
    """The Floor console is signed in as a manager, but the person at the shelf types their own badge:
    the movement is written under their name, and only if they are on the floor of THIS depot."""
    h = login(client)
    make(client, h, "E-1", location_name="Main")
    make(client, h, "E-2", location_name="Spare")
    move = lambda badge, code="WH-01": client.post("/stock-movements", headers=h, json={
        "direction": "receive", "barcode": "A1", "location_kind": "warehouse", "location_code": code,
        "quantity": 2, "operator_badge": badge})
    assert move("e-1").status_code == 422                       # active but not checked in
    client.post("/attendance/check-in", headers=h, json={"badge_id": "E-1", "warehouse_code": "WH-01"})
    client.post("/attendance/check-in", headers=h, json={"badge_id": "E-2", "warehouse_code": "WH-02"})
    ok = move("e-1")
    assert ok.status_code == 201, ok.text
    assert ok.json()["handled_by"] == "Mia Stone · E-1"
    assert move("E-2").status_code == 422                       # works at another depot
    assert move("NOPE").status_code == 422                      # unknown badge
    assert move("E-2", "WH-02").status_code == 201
    sql("UPDATE employees SET is_active = FALSE WHERE badge_id = 'E-1'")
    assert move("E-1").status_code == 422                       # switched off since
    # No badge typed: still the signed-in account.
    plain = client.post("/stock-movements", headers=h, json={
        "direction": "receive", "barcode": "A1", "location_kind": "warehouse", "location_code": "WH-01",
        "quantity": 1})
    assert plain.json()["handled_by"].endswith("· B-001")
    assert sql("SELECT count(*) FROM stock_movements WHERE quantity = 2") == [(2,)]
