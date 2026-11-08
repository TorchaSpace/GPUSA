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


def test_a_till_manages_only_its_own_shop_staff(client):
    h = login(client)
    make(client, h)  # E-100 works at the warehouse "main"
    sql("UPDATE accounts SET role = 'cashier'")
    sql("UPDATE employees SET location_type = 'Dealership', location_name = 'CST-04' WHERE badge_id = 'B-001'")
    pos = login(client, "pos")
    shop = person("S-1", location_type="Dealership", location_name="Coast", national_id="100 000 001 46")
    assert client.post("/employees", headers=pos, json={**shop, "national_id": None}).status_code == 422  # ID needed
    assert client.post("/employees", headers=pos, json={**shop, "national_id": "10000000147"}).status_code == 422
    got = client.post("/employees", headers=pos, json=shop)
    assert got.status_code == 201, got.text
    assert got.json()["national_id_masked"] == "100******46"
    assert sql("SELECT national_id FROM employees WHERE badge_id = 'S-1'") == [("10000000146",)]
    twin = client.post("/employees", headers=pos, json={**shop, "badge_id": "S-2"})
    assert twin.status_code == 409 and "S-1" in twin.text
    # Not its place: adding to the warehouse, moving its own person away, or touching the warehouse's person.
    assert client.post("/employees", headers=pos, json=person("S-3", national_id="12345678950")).status_code == 403
    assert client.patch("/employees/S-1", headers=pos, json={**shop, "location_type": "Warehouse",
                                                               "location_name": "main"}).status_code == 403
    assert client.patch("/employees/E-100", headers=pos, json=person()).status_code == 403
    renamed = client.patch("/employees/S-1", headers=pos, json={**shop, "name": "Su", "title": "Kasa", "national_id": None})
    assert renamed.status_code == 200 and renamed.json()["national_id_masked"] == "100******46"
    # The list is the shop's own people only, IDs masked; another place's person is not found.
    listed = client.get("/employees", headers=pos).json()
    assert [e["badge_id"] for e in listed] == ["B-001", "S-1"]
    assert all("national_id" not in e for e in listed)
    assert client.get("/employees/E-100", headers=pos).status_code == 404
    # The Admin app itself only watches.
    sql("UPDATE accounts SET role = 'admin'")
    assert client.post("/employees", headers=login(client), json=person("A-1")).status_code == 201  # API/CLI still can


def test_a_depot_manager_manages_only_its_warehouse_staff(client):
    h = login(client)
    make(client, h, "E-1", location_name="Main")
    make(client, h, "E-2", location_name="Spare")
    sql("UPDATE accounts SET role = 'depot_manager'")
    sql("UPDATE employees SET location_type = 'Warehouse', location_name = 'WH-01' WHERE badge_id = 'B-001'")
    depot = login(client, "depot_console")
    new = person("E-3", location_name="WH-01", national_id="12345678950")
    assert client.post("/employees", headers=depot, json=new).status_code == 201
    assert client.post("/employees", headers=depot, json={**new, "badge_id": "E-4", "location_name": "Spare",
                                                          "national_id": "23456789138"}).status_code == 403
    assert sorted(e["badge_id"] for e in client.get("/employees", headers=depot).json()) == ["B-001", "E-1", "E-3"]
    assert client.post("/attendance/check-in", headers=depot, json={"badge_id": "E-3"}).status_code == 200
    assert client.post("/attendance/check-in", headers=depot, json={"badge_id": "E-2"}).status_code == 409  # not ours
    assert [o["badge_id"] for o in client.get("/attendance/open", headers=depot).json()] == ["E-3"]
    now = datetime.now(timezone.utc)
    day = {"start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=1)).isoformat()}
    assert [r["badge_id"] for r in client.get("/attendance/records", headers=depot, params=day).json()] == ["E-3"]
    sql("UPDATE accounts SET role = 'admin'")
    assert client.post("/attendance/check-in", headers=login(client), json={"badge_id": "E-2"}).status_code == 200
    assert len(client.get("/attendance/records", headers=login(client), params=day).json()) == 2  # admin sees all


def test_cashier_cannot_change_an_administrator(client):
    h = login(client)
    make(client, h, "S-9", location_type="Dealership", location_name="Coast")
    client.post("/accounts", headers=h, json={"badge_id": "S-9", "role": "cashier", "pin": "583920"})
    sql("UPDATE employees SET location_type = 'Dealership', location_name = 'Coast' WHERE badge_id = 'B-001'")
    pos = till_login(client, "S-9", "583920")
    assert pos.status_code == 200, pos.text
    tok = {"Authorization": f"Bearer {pos.json()['token']}"}
    admin_row = {"name": "Ali", "role": "Management", "location_type": "Dealership", "location_name": "Coast",
                 "is_active": False}
    assert client.patch("/employees/B-001", headers=tok, json=admin_row).status_code == 403


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
    client.post("/accounts", headers=h, json={"badge_id": "E-1", "role": "cashier", "pin": "246802"})
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


def test_a_till_punches_its_own_shop_staff(client):
    h = login(client)
    make(client, h)
    make(client, h, "S-1", location_type="Dealership", location_name="Coast")
    sql("UPDATE accounts SET role = 'cashier'")
    sql("UPDATE employees SET location_type = 'Dealership', location_name = 'Coast' WHERE badge_id = 'B-001'")
    pos = login(client, "pos")
    assert client.post("/attendance/check-in", headers=pos, json={"badge_id": "E-100"}).status_code == 409
    assert client.post("/attendance/check-in", headers=pos,
                       json={"badge_id": "s-1", "dealership_code": "cst-04"}).status_code == 200
    assert client.post("/attendance/check-out", headers=pos, json={"badge_id": "S-1"}).status_code == 200


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
    r = client.post("/accounts", headers=h, json={"badge_id": "e-100", "role": "cashier", "pin": "246802"})
    assert r.status_code == 201, r.text
    assert (r.json()["badge_id"], r.json()["role"], r.json()["is_active"]) == ("E-100", "cashier", True)
    assert client.get("/accounts/without", headers=h).json() == []
    assert till_login(client, "E-100", "246802").status_code == 200
    assert client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"}).status_code == 409


@pytest.mark.parametrize("pin,role", [("12", "cashier"), ("1111", "cashier"), ("1234", "cashier"), ("12ab", "cashier"),
                                      ("123456", "admin"), ("1" * 13, "cashier")])
def test_weak_pins_are_refused(client, pin, role):
    h = login(client)
    make(client, h)
    assert client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": role, "pin": pin}).status_code == 422


def test_admin_sets_someone_elses_pin_and_clears_a_lock(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"})
    for _ in range(5):
        till_login(client, "E-100", "9999")
    assert till_login(client, "E-100", "246802").status_code == 423
    assert client.post("/accounts/E-100/unlock", headers=h).status_code == 204
    assert till_login(client, "E-100", "246802").status_code == 200
    assert client.post("/accounts/E-100/pin", headers=h, json={"pin": "739102"}).status_code == 204
    assert till_login(client, "E-100", "246802").status_code == 401
    assert till_login(client, "E-100", "739102").status_code == 200
    assert client.post("/accounts/B-001/pin", headers=h, json={"pin": "739104"}).status_code == 409  # not your own


def test_change_own_pin_needs_the_current_one(client):
    h = login(client)
    wrong = client.post("/accounts/me/pin", headers=h, json={"current_pin": "000999", "new_pin": "739104"})
    assert wrong.status_code == 401
    assert client.post("/accounts/me/pin", headers=h, json={"current_pin": PIN, "new_pin": "12"}).status_code == 422
    done = client.post("/accounts/me/pin", headers=h, json={"current_pin": PIN, "new_pin": "739104"})
    assert done.status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 401                      # the old token died with the old PIN
    assert client.get("/auth/me", headers={"Authorization": "Bearer " + done.json()["token"]}).status_code == 200
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
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"})
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin"}).status_code == 422
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin", "new_pin": "2468"}).status_code == 422
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "admin", "new_pin": "135792"}).status_code == 204
    assert client.post("/auth/login", json={"badge_id": "E-100", "pin": "135792", "area": "admin"}).status_code == 200
    moved = client.patch("/employees/E-100", headers=h, json={"name": "Ada", "role": "Operations",
                                                                "location_type": "Dealership", "location_name": "CST-04"})
    assert moved.status_code == 200, moved.text
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "depot_manager"}).status_code == 422  # not at a warehouse
    client.patch("/employees/E-100", headers=h, json={"name": "Ada", "role": "Logistics", "location_type": "Warehouse",
                                                       "location_name": "Main"})
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "depot_manager"}).status_code == 204
    assert client.post("/accounts/B-001/role", headers=h, json={"role": "cashier"}).status_code == 409  # not your own
    assert client.post("/accounts/E-100/role", headers=h, json={"role": "wizard"}).status_code == 422


def test_root_is_protected_and_a_second_admin_is_not_root(client):
    h = login(client)
    make(client, h)
    assert client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "admin", "pin": "135792"}).status_code == 201
    r = client.post("/auth/login", json={"badge_id": "E-100", "pin": "135792", "area": "admin"})
    assert r.status_code == 200 and r.json()["is_root"] is False
    assert client.post("/auth/login", json={"badge_id": "B-001", "pin": PIN, "area": "admin"}).json()["is_root"] is True
    second = {"Authorization": "Bearer " + r.json()["token"]}
    # an Admin who is not root cannot manage accounts, root cannot be touched by anyone
    assert client.post("/accounts/B-001/active", headers=second, json={"active": False}).status_code == 403
    assert client.get("/accounts", headers=second).status_code == 403
    assert client.post("/accounts/B-001/role", headers=h, json={"role": "cashier"}).status_code == 409
    assert client.delete("/accounts/B-001", headers=h).status_code == 409
    assert client.post("/accounts/E-100/active", headers=h, json={"active": False}).status_code == 204


def test_switch_off_and_delete_an_account(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"})
    assert client.post("/accounts/E-100/active", headers=h, json={"active": False}).status_code == 204
    assert till_login(client, "E-100", "246802").status_code == 403
    assert client.delete("/accounts/E-100", headers=h).status_code == 204
    assert client.delete("/accounts/E-100", headers=h).status_code == 404
    assert client.delete("/accounts/B-001", headers=h).status_code == 409
    assert client.post("/accounts/B-001/active", headers=h, json={"active": False}).status_code == 409


def test_account_changes_are_logged(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"})
    client.post("/accounts/E-100/pin", headers=h, json={"pin": "739102"})
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


def test_a_depot_manager_cannot_be_moved_off_the_warehouse(client):
    h = login(client)
    make(client, h)
    client.post("/accounts", headers=h, json={"badge_id": "E-100", "role": "depot_manager", "pin": "246802"})
    r = client.patch("/employees/E-100", headers=h, json={"name": "Ada", "role": "Operations", "location_type": "Dealership",
                                                          "location_name": "CST-04"})
    assert r.status_code == 422, r.text


def test_iam_user_opens_admin_but_cannot_manage_accounts(client):
    root = login(client)
    make(client, root)
    assert client.post("/accounts", headers=root, json={"badge_id": "E-100", "role": "cashier", "pin": "246802"}).status_code == 201
    r = client.post("/auth/login", json={"badge_id": "E-100", "pin": "246802", "area": "admin"})
    assert r.status_code == 200, r.text
    iam = {"Authorization": "Bearer " + r.json()["token"]}
    for call in (lambda: client.get("/accounts", headers=iam),
                 lambda: client.get("/accounts/events", headers=iam),
                 lambda: client.post("/accounts", headers=iam, json={"badge_id": "B-001", "role": "cashier", "pin": "246802"}),
                 lambda: client.post("/accounts/E-100/role", headers=iam, json={"role": "depot_manager"}),
                 lambda: client.delete("/accounts/B-001", headers=iam)):
        assert call().status_code == 403
    assert client.post("/accounts", headers=root, json={"badge_id": "B-001", "role": "cashier", "pin": "246802"}).status_code in (409, 422)
