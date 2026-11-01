"""Dealership stock requests: ask, withdraw, decline, list."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import _schema, client, login, sql  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean(client):
    sql("TRUNCATE stock_requests, shipments RESTART IDENTITY CASCADE")


def ask(client, h, quantity=3, code="CST-04", barcode="A1", **extra):
    return client.post("/stock-requests", headers=h,
                       json={"dealership_code": code, "barcode": barcode, "quantity": quantity, **extra})


def test_needs_a_token(client):
    assert client.get("/stock-requests").status_code == 401
    assert client.post("/stock-requests", json={}).status_code == 401
    assert client.post("/stock-requests/1/cancel").status_code == 401
    assert client.post("/stock-requests/1/decline", json={}).status_code == 401


def test_ask_stores_names_and_who_asked(client):
    pos = login(client, "pos")
    r = ask(client, pos, note="  running   low  ", barcode="a1")
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["id"], body["status"], body["dealership_name"], body["product_barcode"], body["product_name"],
            body["quantity"], body["note"], body["requested_by"]) == \
        (1, "open", "Coast", "A1", "Anvil", 3, "running low", "Ali · B-001")
    assert body["shipment_id"] is None and body["shipment_status"] is None


def test_one_open_request_per_dealership_and_product(client):
    pos = login(client, "pos")
    assert ask(client, pos).status_code == 201
    r = ask(client, pos, barcode="a1")
    assert r.status_code == 409 and "RQ-00001" in r.json()["detail"]
    assert client.post("/stock-requests/1/cancel", headers=pos).status_code == 200
    assert ask(client, pos).status_code == 201  # the first one is closed: asking again is fine


@pytest.mark.parametrize("body", [{"quantity": 0}, {"quantity": 100_001}, {"quantity": 2.5}, {"note": "x" * 1001},
                                  {"dealership_code": "  "}, {"barcode": ""}])
def test_bad_input_is_422(client, body):
    pos = login(client, "pos")
    payload = {"dealership_code": "CST-04", "barcode": "A1", "quantity": 1, **body}
    assert client.post("/stock-requests", headers=pos, json=payload).status_code == 422


def test_long_note_after_cleaning_is_422(client):
    pos = login(client, "pos")
    assert ask(client, pos, note="word " * 60).status_code == 422


def test_unknown_or_switched_off_things(client):
    pos = login(client, "pos")
    assert ask(client, pos, code="NOPE").status_code == 404
    assert ask(client, pos, barcode="NOPE").status_code == 404
    sql("UPDATE products SET is_active = FALSE")
    assert ask(client, pos).status_code == 409
    sql("UPDATE products SET is_active = TRUE; UPDATE dealerships SET is_active = FALSE")
    assert ask(client, pos).status_code == 409


def test_only_a_till_asks_and_only_the_depot_declines(client):
    admin_area = login(client, "admin")
    assert ask(client, admin_area).status_code == 403  # asking belongs to a till
    pos = login(client, "pos")
    assert ask(client, pos).status_code == 201
    assert client.post("/stock-requests/1/decline", headers=pos, json={}).status_code == 403
    depot = login(client, "depot_console")
    assert client.post("/stock-requests/1/cancel", headers=depot).status_code == 403
    assert client.post("/stock-requests/1/decline", headers=depot, json={"note": "no truck"}).status_code == 200


def test_decline_records_who_and_why_and_closes(client):
    pos = login(client, "pos")
    ask(client, pos)
    depot = login(client, "depot_console")
    r = client.post("/stock-requests/1/decline", headers=depot, json={"note": " out   of stock "})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decided_by"], r.json()["decision_note"]) == \
        ("declined", "Ali · B-001", "out of stock")
    again = client.post("/stock-requests/1/decline", headers=depot, json={})
    assert again.status_code == 409 and "declined" in again.json()["detail"]
    assert client.post("/stock-requests/1/cancel", headers=pos).status_code == 409
    assert client.post("/stock-requests/99/cancel", headers=pos).status_code == 404


def test_account_switched_off_cannot_finish_a_write(client):
    pos = login(client, "pos")
    sql("UPDATE accounts SET is_active = FALSE")
    assert ask(client, pos).status_code == 401


def test_list_filters_order_and_shipment_status(client):
    pos = login(client, "pos")
    ask(client, pos)
    sql("INSERT INTO products (barcode, name, price) VALUES ('B2', 'Bolt', 1)")
    ask(client, pos, barcode="B2")
    client.post("/stock-requests/1/cancel", headers=pos)
    everything = client.get("/stock-requests", headers=pos).json()
    assert [r["id"] for r in everything] == [2, 1]  # newest first
    open_only = client.get("/stock-requests?status=open", headers=pos).json()
    assert [r["id"] for r in open_only] == [2]
    two = client.get("/stock-requests?status=open&status=cancelled", headers=pos).json()
    assert len(two) == 2
    assert client.get("/stock-requests?dealership_code=OTHER", headers=pos).json() == []
    assert len(client.get("/stock-requests?limit=1", headers=pos).json()) == 1
    assert client.get("/stock-requests?status=bogus", headers=pos).status_code == 422
    # a planned request shows its shipment's own status, never a copy
    sql("INSERT INTO shipments (id, origin, dealership_code, dealership_name, carrier, status, planned_eta, eta) "
        "VALUES (7, 'Main', 'CST-04', 'Coast', 'Truck', 'in_transit', now(), now())")
    sql("UPDATE stock_requests SET status = 'planned', shipment_id = 7 WHERE id = 2")
    one = client.get("/stock-requests/2", headers=pos).json()
    assert (one["status"], one["shipment_id"], one["shipment_status"]) == ("planned", 7, "in_transit")
    assert client.get("/stock-requests/99", headers=pos).status_code == 404
