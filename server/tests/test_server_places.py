"""Dealerships, warehouses, product delete, settings and the admin audit trail."""

import pytest

pytest.importorskip("alembic")

from test_server_purchasing import second_admin_and_manager  # noqa: E402,F401
from test_server_writes import _schema, client, login, sql  # noqa: E402,F401


def dealer(client, h, code=" new-1 ", **extra):
    body = {"code": code, "name": " New  Shop ", "region": "Metro", "city": " Izmir ", "manager_name": " Zed "}
    body.update(extra)
    return client.post("/dealerships", headers=h, json=body)


def wh(client, h, code=" wh-09 ", **extra):
    body = {"code": code, "name": " Spare ", "city": "Bursa", "capacity_units": 100, "docks": 2}
    body.update(extra)
    return client.post("/warehouses", headers=h, json=body)


def test_dealership_create_update_delete(client):
    h = login(client)
    r = dealer(client, h)
    assert r.status_code == 201, r.text
    assert (r.json()["code"], r.json()["name"], r.json()["city"], r.json()["manager_name"]) == (
        "NEW-1", "New Shop", "Izmir", "Zed")
    dup = dealer(client, h, "New-1")
    assert dup.status_code == 409 and "already exists" in dup.json()["detail"]
    up = client.put("/dealerships/new-1", headers=h, json={"code": "IGNORED", "name": "Renamed", "region": "Valley",
                                                           "city": "Ankara", "is_active": False})
    assert up.status_code == 200 and (up.json()["code"], up.json()["name"], up.json()["is_active"]) == (
        "NEW-1", "Renamed", False)
    assert client.put("/dealerships/NOPE", headers=h, json={"code": "x", "name": "n", "region": "Metro"}).status_code == 404
    assert client.get("/dealerships", headers=h).json()
    assert client.delete("/dealerships/new-1", headers=h).status_code == 204
    assert client.delete("/dealerships/new-1", headers=h).status_code == 404
    assert sql("SELECT action, kind, target, detail FROM admin_audit") == [("deleted", "dealership", "NEW-1", "Renamed")]


@pytest.mark.parametrize("bad", [{"code": "  "}, {"name": " "}, {"region": "Moon"}, {"code": "X" * 25},
                                 {"city": "c" * 121}])
def test_dealership_validation(client, bad):
    assert dealer(client, login(client), **bad).status_code == 422


def test_dealership_delete_guards_and_cleanup(client):
    h = login(client)
    dealer(client, h)
    sql("UPDATE stock_levels SET quantity = 0")
    sql("INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) VALUES ('dealership', 'NEW-1', 'A1', 3)")
    held = client.delete("/dealerships/NEW-1", headers=h)
    assert held.status_code == 409 and "still holds 3 units" in held.json()["detail"]
    sql("UPDATE stock_levels SET quantity = 0 WHERE location_code = 'NEW-1'")
    sql("INSERT INTO shipments (origin, dealership_code, dealership_name, carrier, status, planned_eta, eta, origin_code) "
        "VALUES ('WH-01', 'NEW-1', 'New', 'C', 'scheduled', now(), now(), 'WH-01')")
    busy = client.delete("/dealerships/NEW-1", headers=h)
    assert busy.status_code == 409 and "1 active shipments" in busy.json()["detail"]
    sql("UPDATE shipments SET status = 'delivered'")
    sql("TRUNCATE stock_requests, purchase_orders, shipments RESTART IDENTITY CASCADE")
    sql("INSERT INTO stock_requests (dealership_code, dealership_name, product_barcode, product_name, quantity) "
        "VALUES ('NEW-1', 'New', 'A1', 'Anvil', 2)")
    assert client.delete("/dealerships/NEW-1", headers=h).status_code == 204
    assert sql("SELECT status, decision_note FROM stock_requests") == [("cancelled", "Dealership removed")]
    assert sql("SELECT count(*) FROM stock_levels WHERE location_code = 'NEW-1'") == [(0,)]


def test_warehouse_lifecycle_and_guards(client):
    h = login(client)
    sql("TRUNCATE stock_requests, purchase_orders, shipments RESTART IDENTITY CASCADE")
    r = wh(client, h)
    assert r.status_code == 201, r.text
    assert (r.json()["code"], r.json()["name"], r.json()["capacity_units"]) == ("WH-09", "Spare", 100)
    assert wh(client, h, "WH-09").status_code == 409
    assert wh(client, h, "wh-10", capacity_units=0).status_code == 422
    assert wh(client, h, "wh-10", docks=-1).status_code == 422
    sql("INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) VALUES ('warehouse', 'WH-09', 'A1', 40)")
    low = client.put("/warehouses/wh-09", headers=h, json={"code": "x", "name": "Spare", "capacity_units": 30})
    assert low.status_code == 409 and "over the capacity" in low.json()["detail"]
    ok = client.put("/warehouses/wh-09", headers=h, json={"code": "x", "name": "Spare 2", "capacity_units": None, "docks": 3})
    assert ok.status_code == 200 and (ok.json()["name"], ok.json()["capacity_units"], ok.json()["docks"]) == ("Spare 2", None, 3)
    assert client.put("/warehouses/NOPE", headers=h, json={"code": "x", "name": "n"}).status_code == 404
    assert "still holds 40 units" in client.delete("/warehouses/WH-09", headers=h).json()["detail"]
    sql("UPDATE stock_levels SET quantity = 0 WHERE location_code = 'WH-09'")
    sql("INSERT INTO purchase_orders (product_barcode, product_name_at_order, supplier, quantity, unit_price, site, status) "
        "VALUES ('A1', 'Anvil', 'Acme', 5, 1, 'WH-09 · Spare 2', 'sent')")
    assert "1 open purchase orders" in client.delete("/warehouses/wh-09", headers=h).json()["detail"]
    sql("UPDATE purchase_orders SET status = 'received'")
    sql("INSERT INTO shipments (origin, dealership_code, dealership_name, carrier, status, planned_eta, eta, origin_code) "
        "VALUES ('WH-09', 'CST-04', 'x', 'C', 'in_transit', now(), now(), 'WH-09')")
    assert "1 active shipments" in client.delete("/warehouses/wh-09", headers=h).json()["detail"]
    sql("UPDATE shipments SET status = 'cancelled'")
    assert client.delete("/warehouses/wh-09", headers=h).status_code == 204
    assert sql("SELECT kind, target FROM admin_audit") == [("warehouse", "WH-09")]


def test_product_delete_only_when_never_used(client):
    h = login(client)
    sql("TRUNCATE stock_movements, purchase_orders, shipments, transaction_items RESTART IDENTITY CASCADE")
    sql("UPDATE stock_levels SET quantity = 0")
    sql("UPDATE products SET stock_quantity = 0")
    assert client.post("/products", headers=h, json={"barcode": "fresh", "name": "Fresh", "price": 1}).status_code == 201
    assert client.delete("/products/FRESH", headers=h).status_code == 204
    assert client.delete("/products/FRESH", headers=h).status_code == 404
    sql("INSERT INTO stock_movements (product_barcode, movement_type, quantity) VALUES ('A1', 'receive', 1)")
    used = client.delete("/products/A1", headers=h)
    assert used.status_code == 409 and "can't be deleted" in used.json()["detail"]
    sql("TRUNCATE stock_movements RESTART IDENTITY")
    sql("UPDATE products SET stock_quantity = 5 WHERE barcode = 'A1'")
    assert "5 units are still in stock" in client.delete("/products/A1", headers=h).json()["detail"]
    assert sql("SELECT action, kind, target FROM admin_audit ORDER BY id") == [
        ("created", "product", "FRESH"), ("deleted", "product", "FRESH")]


def test_settings_public_private_and_secret(client):
    h = login(client)
    sql("TRUNCATE app_settings")
    sql("INSERT INTO app_settings (key, value) VALUES ('recovery.code_hash', 'SECRET')")
    assert client.put("/settings", headers=h, json={"values": {"ui.language": "tr", "store.name": "Shop",
                                                               "notifications.read.B-001": "7"}}).status_code == 200
    assert client.put("/settings", headers=h, json={"values": {"recovery.code_hash": "x"}}).status_code == 422
    public = client.get("/settings").json()
    assert public == {"ui.language": "tr", "store.name": "Shop"}
    assert client.get("/settings/private", headers=h).json() == {"notifications.read.B-001": "7"}
    assert client.get("/settings/private").status_code in (401, 403)
    client.put("/settings", headers=h, json={"values": {"ui.language": "en"}})
    assert client.get("/settings").json()["ui.language"] == "en"
    mgr, _ = second_admin_and_manager(client, h)
    assert client.put("/settings", headers=mgr, json={"values": {"ui.language": "tr"}}).status_code == 403
    assert client.get("/settings/private", headers=mgr).status_code == 403


def test_audit_listing_and_roles(client):
    h = login(client)
    dealer(client, h)
    client.delete("/dealerships/NEW-1", headers=h)
    wh(client, h)
    client.delete("/warehouses/WH-09", headers=h)
    rows = client.get("/audit", headers=h).json()
    assert [r["kind"] for r in rows] == ["warehouse", "dealership"] and rows[0]["actor"] == "Ali · B-001"
    assert [r["kind"] for r in client.get("/audit?kind=dealership&limit=5", headers=h).json()] == ["dealership"]
    mgr, _ = second_admin_and_manager(client, h)
    assert client.get("/audit", headers=mgr).status_code == 403
    for call in (lambda: dealer(client, mgr, "q-1"), lambda: wh(client, mgr, "q-2"),
                 lambda: client.delete("/products/A1", headers=mgr)):
        assert call().status_code == 403
