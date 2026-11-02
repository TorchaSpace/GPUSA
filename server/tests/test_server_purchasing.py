"""Price bands, purchase orders and their deliveries."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import PIN, _schema, client, level, login, sql, total  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean_tables(client):
    sql("TRUNCATE purchase_orders, purchase_price_ranges, activity_events RESTART IDENTITY CASCADE")


def band(client, h, low=4, high=6, supplier=" Acme "):
    return client.put("/price-ranges/a1", headers=h, json={"min_unit_price": low, "max_unit_price": high,
                                                           "default_supplier": supplier})


def order(client, h, price=5, qty=10, **extra):
    return client.post("/purchase-orders", headers=h, json={
        "barcode": "a1", "supplier": " Acme ", "quantity": qty, "unit_price": price, "site": "wh-01 · main", **extra})


def second_admin_and_manager(client, admin):
    """A depot manager (M-1) with their own account."""
    client.post("/employees", headers=admin, json={"badge_id": "M-1", "name": "Mo", "role": "Logistics",
                                                  "location_type": "Warehouse", "location_name": "Main"})
    client.post("/employees", headers=admin, json={"badge_id": "M-2", "name": "Mia", "role": "Logistics",
                                                  "location_type": "Warehouse", "location_name": "Main"})
    for badge in ("M-1", "M-2"):
        r = client.post("/accounts", headers=admin, json={"badge_id": badge, "role": "depot_manager", "pin": "2468"})
        assert r.status_code == 201, r.text
    login_as = lambda badge: {"Authorization": "Bearer " + client.post(  # noqa: E731
        "/auth/login", json={"badge_id": badge, "pin": "2468", "area": "depot_console"}).json()["token"]}
    return login_as("M-1"), login_as("M-2")


# --- bands ----------------------------------------------------------------------------

def test_set_get_and_delete_a_band(client):
    h = login(client)
    r = band(client, h)
    assert r.status_code == 200, r.text
    assert r.json() == {"product_barcode": "A1", "min_unit_price": 4.0, "max_unit_price": 6.0, "default_supplier": "Acme"}
    assert client.get("/price-ranges", headers=h).json()[0]["product_barcode"] == "A1"
    assert client.get("/price-ranges/a1", headers=h).json()["max_unit_price"] == 6.0
    assert client.get("/price-ranges/zz", headers=h).json() is None
    band(client, h, 4, 6)  # unchanged: no second audit row
    band(client, h, 3, 7)
    assert sql("SELECT action, detail FROM admin_audit ORDER BY id") == [("created", "4-6"), ("changed", "4-6 -> 3-7")]
    assert client.delete("/price-ranges/A1", headers=h).status_code == 204
    assert client.get("/price-ranges/a1", headers=h).json() is None
    assert sql("SELECT action FROM admin_audit ORDER BY id DESC LIMIT 1") == [("deleted",)]


def test_band_validation_and_roles(client):
    h = login(client)
    assert band(client, h, 7, 3).status_code == 422
    assert band(client, h, -1, 3).status_code == 422
    assert client.put("/price-ranges/NOPE", headers=h, json={"min_unit_price": 1, "max_unit_price": 2}).status_code == 404
    mgr, _ = second_admin_and_manager(client, h)
    assert client.put("/price-ranges/A1", headers=mgr, json={"min_unit_price": 1, "max_unit_price": 2}).status_code == 403
    assert client.get("/price-ranges", headers=mgr).status_code == 200
    sql("UPDATE accounts SET role = 'cashier' WHERE employee_id = 1")
    assert client.get("/price-ranges", headers=login(client, "pos")).status_code == 403


# --- orders ---------------------------------------------------------------------------

def test_in_band_is_sent_out_of_band_is_held(client):
    h = login(client)
    held = order(client, h)
    assert held.status_code == 201 and held.json()["status"] == "pending" and held.json()["hold_reason"] == "no_range"
    band(client, h)
    ok = order(client, h, 5).json()
    assert (ok["status"], ok["hold_reason"], ok["site"], ok["supplier"], ok["range_min"], ok["range_max"]) == (
        "sent", None, "WH-01 · MAIN", "Acme", 4.0, 6.0)
    assert order(client, h, 6.01).json()["hold_reason"] == "above_range"
    assert order(client, h, 3.99).json()["hold_reason"] == "below_range"
    assert order(client, h, 6).json()["status"] == "sent"  # the ends are inclusive
    assert client.get("/purchase-orders/pending-ids", headers=h).json() == [1, 3, 4]
    assert sql("SELECT kind, severity FROM activity_events ORDER BY id")[:2] == [("po_pending", "warning"), ("po_sent", "info")]


@pytest.mark.parametrize("bad", [{"supplier": "  "}, {"site": " "}, {"quantity": 0}, {"unit_price": 0},
                                 {"unit_price": 0.004}, {"supplier": "x" * 201}])
def test_order_validation(client, bad):
    h = login(client)
    body = {"barcode": "A1", "supplier": "Acme", "quantity": 1, "unit_price": 5, "site": "WH-01", **bad}
    assert client.post("/purchase-orders", headers=h, json=body).status_code == 422


def test_unknown_or_switched_off_product(client):
    h = login(client)
    assert client.post("/purchase-orders", headers=h, json={"barcode": "NOPE", "supplier": "A", "quantity": 1,
                                                            "unit_price": 1, "site": "WH-01"}).status_code == 404
    sql("UPDATE products SET is_active = FALSE")
    assert order(client, h).status_code == 409


def test_only_depot_roles_raise_orders(client):
    h = login(client)
    sql("UPDATE accounts SET role = 'cashier'")
    assert order(client, login(client, "pos")).status_code == 403
    assert client.get("/purchase-orders", headers=login(client, "pos")).status_code == 403


def test_admin_decides_once(client):
    h = login(client)
    oid = order(client, h).json()["id"]
    r = client.post(f"/purchase-orders/{oid}/approve", headers=h, json={"note": " fine "})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decision_note"], r.json()["decided_by"]) == ("sent", "fine", "Ali · B-001")
    again = client.post(f"/purchase-orders/{oid}/reject", headers=h, json={})
    assert again.status_code == 409 and "decided already" in again.json()["detail"]
    other = order(client, h).json()["id"]
    assert client.post(f"/purchase-orders/{other}/reject", headers=h, json={"note": "too dear"}).json()["status"] == "rejected"
    assert sql("SELECT kind FROM activity_events ORDER BY id DESC LIMIT 1") == [("po_rejected",)]
    mgr, _ = second_admin_and_manager(client, h)
    third = order(client, h).json()["id"]
    assert client.post(f"/purchase-orders/{third}/approve", headers=mgr, json={}).status_code == 403
    assert client.post("/purchase-orders/999/approve", headers=h, json={}).status_code == 404


def test_receiving_books_stock_cost_and_status(client):
    h = login(client)
    band(client, h)
    oid = order(client, h, 5, qty=10).json()["id"]
    sql("UPDATE products SET cost_price = 3, stock_quantity = 10")
    r = client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 4, "warehouse_code": "wh-02"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["received_qty"], r.json()["received_by"]) == ("partially_received", 4, "Ali · B-001")
    assert level("warehouse", "WH-02", "A1") == 4 and total("A1") == 14
    assert float(sql("SELECT cost_price FROM products")[0][0]) == pytest.approx(3.57)  # (10*3 + 4*5) / 14
    assert sql("SELECT movement_type, quantity, reference FROM stock_movements") == [("receive", 4, "PO-00001")]
    over = client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 7, "warehouse_code": "WH-02"})
    assert over.status_code == 409 and "awaits 6" in over.json()["detail"]
    done = client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 6, "warehouse_code": "WH-02"})
    assert done.json()["status"] == "received"
    assert client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 1, "warehouse_code": "WH-02"}).status_code == 409


def test_a_held_order_cannot_be_received_and_a_full_warehouse_refuses(client):
    h = login(client)
    held = order(client, h).json()["id"]
    assert client.post(f"/purchase-orders/{held}/receive", headers=h, json={"quantity": 1, "warehouse_code": "WH-02"}).status_code == 409
    band(client, h)
    oid = order(client, h, 5, qty=50).json()["id"]
    full = client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 11, "warehouse_code": "WH-01"})
    assert full.status_code == 409 and "do not fit" in full.json()["detail"]  # WH-01 holds 10
    assert sql("SELECT received_qty FROM purchase_orders WHERE id = %s", (oid,)) == [(0,)]  # nothing kept
    assert client.post(f"/purchase-orders/{oid}/receive", headers=h, json={"quantity": 1, "warehouse_code": "NOPE"}).status_code == 404


def test_floor_delivery_settles_open_orders_oldest_first_then_plain_receipt(client):
    h = login(client)
    band(client, h)
    first = order(client, h, 5, qty=3).json()["id"]
    second = order(client, h, 5, qty=4).json()["id"]
    elsewhere = client.post("/purchase-orders", headers=h, json={"barcode": "A1", "supplier": "Z", "quantity": 9,
                                                                  "unit_price": 5, "site": "WH-02"}).json()["id"]
    r = client.post("/purchase-orders/receive-delivery", headers=h, json={"warehouse_code": "WH-01", "barcode": "a1",
                                                                          "quantity": 9, "reference": "DN-7"})
    assert r.status_code == 200, r.text
    assert r.json() == {"orders": ["PO-00001", "PO-00002"]}
    status = dict(sql("SELECT id, status FROM purchase_orders"))
    assert (status[first], status[second], status[elsewhere]) == ("received", "received", "sent")
    assert level("warehouse", "WH-01", "A1") == 9 and total("A1") == 9
    refs = sql("SELECT quantity, reference FROM stock_movements ORDER BY id")
    assert refs == [(3, "PO-00001"), (4, "PO-00002"), (2, "DN-7")]  # the extra 2 units: an ordinary receipt


def test_cancel_rules(client):
    h = login(client)
    mgr, other = second_admin_and_manager(client, h)
    mine = client.post("/purchase-orders", headers=mgr, json={"barcode": "A1", "supplier": "A", "quantity": 1,
                                                              "unit_price": 5, "site": "WH-01"}).json()["id"]
    assert client.post(f"/purchase-orders/{mine}/cancel", headers=other).status_code == 403  # not theirs
    assert client.post(f"/purchase-orders/{mine}/cancel", headers=mgr).json()["status"] == "cancelled"
    assert client.post(f"/purchase-orders/{mine}/cancel", headers=h).status_code == 409  # already cancelled
    band(client, h)
    sent = order(client, h, 5, qty=5).json()["id"]
    assert client.post(f"/purchase-orders/{sent}/cancel", headers=mgr).status_code == 403  # sent: admin only
    client.post(f"/purchase-orders/{sent}/receive", headers=h, json={"quantity": 2, "warehouse_code": "WH-02"})
    cancelled = client.post(f"/purchase-orders/{sent}/cancel", headers=h).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancelled_by"] == "Ali · B-001"
    assert level("warehouse", "WH-02", "A1") == 2  # what arrived stays
    assert client.post(f"/purchase-orders/{sent}/receive", headers=h, json={"quantity": 1, "warehouse_code": "WH-02"}).status_code == 409


def test_list_and_get(client):
    h = login(client)
    band(client, h)
    order(client, h, 5)
    order(client, h, 9)
    client.post("/purchase-orders", headers=h, json={"barcode": "A1", "supplier": "Z", "quantity": 1,
                                                     "unit_price": 5, "site": "WH-02 · Spare"})
    assert [o["id"] for o in client.get("/purchase-orders", headers=h).json()] == [3, 2, 1]  # newest first
    assert [o["id"] for o in client.get("/purchase-orders", headers=h, params={"status": "pending"}).json()] == [2]
    assert [o["id"] for o in client.get("/purchase-orders", headers=h, params={"site": "wh-01"}).json()] == [2, 1]
    assert [o["id"] for o in client.get("/purchase-orders", headers=h, params={"site": "WH-01", "limit": 1}).json()] == [2]
    assert client.get("/purchase-orders/1", headers=h).json()["product_name"] == "Anvil"
    assert client.get("/purchase-orders/99", headers=h).status_code == 404
