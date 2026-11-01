"""Shipments: plan, dispatch, re-time, cancel, receive - and the stock they move."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("alembic")

from test_server_writes import _schema, client, level, login, sql, total  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean(client):
    sql("TRUNCATE stock_requests, shipments, stock_levels RESTART IDENTITY CASCADE")
    sql("UPDATE products SET stock_quantity = 0")
    sql("INSERT INTO products (barcode, name, price) VALUES ('B2', 'Bolt', 1)")


def eta(hours=2):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def stock(client, h, barcode="A1", qty=5):
    r = client.post("/stock-movements", headers=h, json={"direction": "receive", "barcode": barcode,
                                                          "location_kind": "warehouse", "location_code": "WH-02",
                                                          "quantity": qty})
    assert r.status_code == 201, r.text


def plan(client, h, lines=(("A1", 3),), **extra):
    body = {"origin": "WH-02 · Spare", "origin_code": "WH-02", "dealership_code": "CST-04", "carrier": "Truck",
            "eta": eta(), "lines": [{"barcode": b, "quantity": q} for b, q in lines], **extra}
    return client.post("/shipments", headers=h, json=body)


def test_needs_a_token(client):
    assert client.get("/shipments").status_code == 401
    assert client.post("/shipments", json={}).status_code == 401
    assert client.post("/shipments/1/dispatch").status_code == 401
    assert client.post("/shipments/1/receive", json={}).status_code == 401


def test_plan_moves_nothing_and_merges_lines(client):
    h = login(client)
    stock(client, h)
    r = plan(client, h, lines=(("a1", 1), ("A1", 2), ("b2", 4)), driver="  Hakan ")
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["id"], body["status"], body["driver"], body["dealership_name"], body["stock_moved"]) == \
        (1, "scheduled", "Hakan", "Coast", False)
    assert [(l["product_barcode"], l["product_name"], l["expected_qty"], l["received_qty"]) for l in body["lines"]] == \
        [("A1", "Anvil", 3, None), ("B2", "Bolt", 4, None)]
    assert body["planned_eta"] == body["eta"]
    assert level("warehouse", "WH-02", "A1") == 5  # a plan is only a plan


@pytest.mark.parametrize("change", [{"carrier": "  "}, {"origin": " "}, {"lines": []}, {"eta": "yesterday"},
                                    {"eta": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}])
def test_bad_plan_is_422(client, change):
    h = login(client)
    body = {"origin": "WH-02", "origin_code": "WH-02", "dealership_code": "CST-04", "carrier": "T", "eta": eta(),
            "lines": [{"barcode": "A1", "quantity": 1}], **change}
    assert client.post("/shipments", headers=h, json=body).status_code == 422


def test_eta_before_the_departure_is_422(client):
    h = login(client)
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    assert plan(client, h, eta=eta(1), departure=soon).status_code == 422


def test_unknown_or_switched_off_things(client):
    h = login(client)
    assert plan(client, h, origin_code="WH-99").status_code == 404
    assert plan(client, h, dealership_code="NOPE").status_code == 404
    assert plan(client, h, lines=(("NOPE", 1),)).status_code == 404
    sql("UPDATE products SET is_active = FALSE WHERE barcode = 'B2'")
    assert plan(client, h, lines=(("B2", 1),)).status_code == 409
    sql("UPDATE dealerships SET is_active = FALSE")
    assert plan(client, h).status_code == 409
    assert sql("SELECT count(*) FROM shipments")[0][0] == 0


def test_dispatch_takes_stock_off_the_origin_and_keeps_the_total(client):
    h = login(client)
    stock(client, h)
    assert total("A1") == 5
    plan(client, h)
    r = client.post("/shipments/1/dispatch", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["stock_moved"]) == ("in_transit", True)
    assert r.json()["departed_at"] is not None
    assert level("warehouse", "WH-02", "A1") == 2 and total("A1") == 5  # on the truck, still ours
    assert sql("SELECT movement_type, quantity, reason, reference, handled_by FROM stock_movements "
               "WHERE reason = 'shipment'") == [("dispatch", 3, "shipment", "SH-00001", "Ali · B-001")]
    assert client.post("/shipments/1/dispatch", headers=h).status_code == 409  # not twice


def test_dispatch_short_of_stock_writes_nothing(client):
    h = login(client)
    stock(client, h, "A1", 5)
    stock(client, h, "B2", 1)
    plan(client, h, lines=(("A1", 3), ("B2", 2)))
    assert client.post("/shipments/1/dispatch", headers=h).status_code == 409
    assert level("warehouse", "WH-02", "A1") == 5 and level("warehouse", "WH-02", "B2") == 1
    assert client.get("/shipments/1", headers=h).json()["status"] == "scheduled"


def test_no_origin_warehouse_draws_from_unassigned_stock(client):
    h = login(client)
    client.post("/stock-movements", headers=h, json={"direction": "receive", "barcode": "A1",
                                                     "location_kind": "unassigned", "quantity": 4})
    plan(client, h, origin_code=None)
    assert client.post("/shipments/1/dispatch", headers=h).status_code == 200
    assert level("unassigned", "", "A1") == 1


def test_receive_in_full_puts_the_units_on_the_shelf(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    client.post("/shipments/1/dispatch", headers=h)
    pos = login(client, "pos")
    r = client.post("/shipments/1/receive", headers=pos, json={"note": "  all   there "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["receipt_note"], body["stock_moved"]) == ("delivered", "all there", False)
    assert body["lines"][0]["received_qty"] == 3 and body["delivered_at"] is not None
    assert level("dealership", "CST-04", "A1") == 3 and total("A1") == 5
    assert client.post("/shipments/1/receive", headers=pos, json={}).status_code == 409  # not twice
    assert level("dealership", "CST-04", "A1") == 3


def test_a_shortfall_is_written_off(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    client.post("/shipments/1/dispatch", headers=h)
    pos = login(client, "pos")
    r = client.post("/shipments/1/receive", headers=pos, json={"received": {"a1": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["received_qty"] == 1
    assert level("dealership", "CST-04", "A1") == 1 and total("A1") == 3
    assert sql("SELECT quantity, reason FROM stock_movements WHERE reason = 'discrepancy'") == [(2, "discrepancy")]


def test_receiving_more_than_shipped_or_unknown_lines_is_refused(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    client.post("/shipments/1/dispatch", headers=h)
    pos = login(client, "pos")
    assert client.post("/shipments/1/receive", headers=pos, json={"received": {"A1": 4}}).status_code == 422
    assert client.post("/shipments/1/receive", headers=pos, json={"received": {"B2": 1}}).status_code == 422
    assert client.post("/shipments/1/receive", headers=pos, json={"received": {"A1": -1}}).status_code == 422
    assert level("dealership", "CST-04", "A1") == 0
    assert client.get("/shipments/1", headers=h).json()["status"] == "in_transit"


def test_a_shipment_that_never_left_cannot_be_received(client):
    h = login(client)
    plan(client, h)
    pos = login(client, "pos")
    assert client.post("/shipments/1/receive", headers=pos, json={}).status_code == 409


def test_cancel_a_dispatched_shipment_returns_the_goods(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    client.post("/shipments/1/dispatch", headers=h)
    r = client.post("/shipments/1/cancel", headers=h)
    assert r.status_code == 200 and (r.json()["status"], r.json()["stock_moved"]) == ("cancelled", False)
    assert level("warehouse", "WH-02", "A1") == 5 and total("A1") == 5
    assert client.post("/shipments/1/cancel", headers=h).status_code == 409
    assert client.post("/shipments/1/dispatch", headers=h).status_code == 409


def test_cancel_before_dispatch_moves_nothing(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    assert client.post("/shipments/1/cancel", headers=h).status_code == 200
    assert level("warehouse", "WH-02", "A1") == 5


def test_retime_keeps_the_promise(client):
    h = login(client)
    plan(client, h)
    later = eta(30)
    r = client.post("/shipments/1/eta", headers=h, json={"eta": later})
    assert r.status_code == 200, r.text
    assert r.json()["eta"] != r.json()["planned_eta"]
    assert client.post("/shipments/1/eta", headers=h,
                       json={"eta": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}).status_code == 422
    client.post("/shipments/1/cancel", headers=h)
    assert client.post("/shipments/1/eta", headers=h, json={"eta": later}).status_code == 409


def test_requests_are_planned_and_reopen_on_cancel(client):
    h = login(client)
    pos = login(client, "pos")
    client.post("/stock-requests", headers=pos, json={"dealership_code": "CST-04", "barcode": "A1", "quantity": 3})
    assert plan(client, h, request_ids=[1]).status_code == 201
    one = client.get("/stock-requests/1", headers=h).json()
    assert (one["status"], one["shipment_id"], one["shipment_status"]) == ("planned", 1, "scheduled")
    client.post("/shipments/1/cancel", headers=h)
    one = client.get("/stock-requests/1", headers=h).json()
    assert (one["status"], one["shipment_id"]) == ("open", None)


def test_cancel_with_a_newer_request_for_the_same_product_closes_the_old_one(client):
    h = login(client)
    pos = login(client, "pos")
    client.post("/stock-requests", headers=pos, json={"dealership_code": "CST-04", "barcode": "A1", "quantity": 3})
    plan(client, h, request_ids=[1])
    client.post("/stock-requests", headers=pos, json={"dealership_code": "CST-04", "barcode": "A1", "quantity": 1})
    client.post("/shipments/1/cancel", headers=h)
    assert [r["status"] for r in client.get("/stock-requests?status=open", headers=h).json()] == ["open"]
    assert client.get("/stock-requests/1", headers=h).json()["status"] == "cancelled"


def test_a_bad_request_link_refuses_the_whole_shipment(client):
    h = login(client)
    pos = login(client, "pos")
    client.post("/stock-requests", headers=pos, json={"dealership_code": "CST-04", "barcode": "B2", "quantity": 1})
    assert plan(client, h, request_ids=[1]).status_code == 409   # the product is not on the shipment
    assert plan(client, h, request_ids=[99]).status_code == 404
    assert sql("SELECT count(*) FROM shipments")[0][0] == 0
    assert client.get("/stock-requests/1", headers=h).json()["status"] == "open"


def test_lists_and_counts(client):
    h = login(client)
    plan(client, h)
    plan(client, h, eta=eta(1))
    client.post("/shipments/1/cancel", headers=h)
    assert [s["id"] for s in client.get("/shipments", headers=h).json()] == [2, 1]
    assert [s["id"] for s in client.get("/shipments?status=scheduled", headers=h).json()] == [2]
    assert [s["id"] for s in client.get("/shipments?status=scheduled&status=cancelled", headers=h).json()] == [2, 1]
    assert client.get("/shipments?dealership_code=ZZ", headers=h).json() == []
    assert client.get("/shipments/incoming-count", headers=h).json() == {"count": 1}
    assert client.get("/shipments/incoming-count?dealership_code=ZZ", headers=h).json() == {"count": 0}
    assert client.get("/shipments/99", headers=h).status_code == 404


def test_who_may_do_what(client):
    h = login(client)
    stock(client, h)
    plan(client, h)
    pos = login(client, "pos")
    assert plan(client, pos).status_code == 403
    assert client.post("/shipments/1/dispatch", headers=pos).status_code == 403
    assert client.post("/shipments/1/cancel", headers=pos).status_code == 403
    assert client.post("/shipments/1/receive", headers=h, json={}).status_code == 403  # admin area, not a till
    assert client.get("/shipments", headers=pos).status_code == 200
