"""Shipments (plan / dispatch / re-time / cancel / receive) and dealership stock requests."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("alembic")

from test_server_writes import _schema, client, level, login, move, sql, total  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean_tables(client):
    sql("TRUNCATE shipments, shipment_lines, stock_requests, activity_events RESTART IDENTITY CASCADE")


def soon(hours=24):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def plan(client, h, lines=(("A1", 3),), **extra):
    body = {"origin": "WH-01 · Main", "origin_code": "WH-01", "dealership_code": "cst-04", "carrier": " Fast ",
            "eta": soon(), "lines": [{"barcode": b, "quantity": q} for b, q in lines], **extra}
    return client.post("/shipments", headers=h, json=body)


def till(client):
    return login(client, "pos")


def stocked(client, h, qty=8):
    assert move(client, h, "receive", qty).status_code == 201


def test_plan_moves_nothing_and_merges_lines(client):
    h = login(client)
    stocked(client, h)
    sql("DELETE FROM activity_events")  # the stock-in that set this up is in the feed too; watch only the plan
    r = plan(client, h, lines=(("a1", 2), ("A1", 1)))
    assert r.status_code == 201, r.text
    s = r.json()
    assert (s["status"], s["carrier"], s["dealership_name"], s["stock_moved"]) == ("scheduled", "Fast", "Coast", False)
    assert [(l["product_barcode"], l["expected_qty"]) for l in s["lines"]] == [("A1", 3)]
    assert level("warehouse", "WH-01", "A1") == 8
    assert sql("SELECT kind FROM activity_events") == [("shipment_created",)]


def test_dispatch_takes_stock_then_receive_puts_it_on_the_shelf(client):
    h = login(client)
    stocked(client, h)
    sid = plan(client, h).json()["id"]
    r = client.post(f"/shipments/{sid}/dispatch", headers=h)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["stock_moved"]) == ("in_transit", True)
    assert (level("warehouse", "WH-01", "A1"), total("A1")) == (5, 8)  # on the truck: in the total, at no place
    assert client.post(f"/shipments/{sid}/dispatch", headers=h).status_code == 409  # not twice
    r = client.post(f"/shipments/{sid}/receive", headers=till(client), json={"received": {"a1": 2}, "note": "one broke"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["status"], got["receipt_note"], got["lines"][0]["received_qty"]) == ("delivered", "one broke", 2)
    assert (level("dealership", "CST-04", "A1"), total("A1")) == (2, 7)  # the unit that never arrived is written off
    assert sql("SELECT reason, quantity FROM stock_movements WHERE reason = 'discrepancy'") == [("discrepancy", 1)]
    assert client.post(f"/shipments/{sid}/receive", headers=till(client), json={}).status_code == 409  # not twice
    assert ("shipment_short",) in sql("SELECT kind FROM activity_events")


def test_dispatch_short_of_stock_writes_nothing(client):
    h = login(client)
    stocked(client, h, 2)
    sid = plan(client, h, lines=(("A1", 3),)).json()["id"]
    r = client.post(f"/shipments/{sid}/dispatch", headers=h)
    assert r.status_code == 409 and "Only 2 units of A1" in r.json()["detail"]
    assert (level("warehouse", "WH-01", "A1"), client.get(f"/shipments/{sid}", headers=h).json()["status"]) \
        == (2, "scheduled")


def test_receive_rules(client):
    h = login(client)
    stocked(client, h)
    sid = plan(client, h).json()["id"]
    assert client.post(f"/shipments/{sid}/receive", headers=till(client), json={}).status_code == 409  # never dispatched
    client.post(f"/shipments/{sid}/dispatch", headers=h)
    assert client.post(f"/shipments/{sid}/receive", headers=till(client), json={"received": {"A1": 4}}).status_code == 422
    assert client.post(f"/shipments/{sid}/receive", headers=till(client), json={"received": {"ZZ": 1}}).status_code == 422
    assert client.post(f"/shipments/{sid}/receive", headers=till(client), json={"received": {"A1": -1}}).status_code == 422
    r = client.post(f"/shipments/{sid}/receive", headers=till(client), json={})  # a line left out = received in full
    assert r.status_code == 200 and level("dealership", "CST-04", "A1") == 3
    # a depot console is not a till
    assert client.post(f"/shipments/{sid}/receive", headers=login(client, "depot_console"), json={}).status_code == 403


def test_cancel_after_dispatch_returns_the_goods(client):
    h = login(client)
    stocked(client, h)
    sid = plan(client, h).json()["id"]
    client.post(f"/shipments/{sid}/dispatch", headers=h)
    r = client.post(f"/shipments/{sid}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (level("warehouse", "WH-01", "A1"), total("A1")) == (8, 8)
    assert client.post(f"/shipments/{sid}/cancel", headers=h).status_code == 409


def test_retime_keeps_the_original_promise(client):
    h = login(client)
    sid = plan(client, h).json()["id"]
    later = soon(48)
    r = client.post(f"/shipments/{sid}/eta", headers=h, json={"eta": later})
    assert r.status_code == 200
    assert r.json()["planned_eta"] != r.json()["eta"]
    assert client.post(f"/shipments/{sid}/eta", headers=h,
                       json={"eta": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}).status_code == 422


def test_plan_validation(client):
    h = login(client)
    assert plan(client, h, dealership_code="NOPE").status_code in (404, 422)
    assert client.post("/shipments", headers=h, json={
        "origin": "x", "dealership_code": "NOPE", "carrier": "c", "eta": soon(),
        "lines": [{"barcode": "A1", "quantity": 1}]}).status_code == 404
    assert plan(client, h, lines=(("NOPE", 1),)).status_code == 404
    assert plan(client, h, lines=()).status_code == 422
    assert plan(client, h, eta=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat()).status_code == 422
    sql("UPDATE dealerships SET is_active = FALSE")
    assert plan(client, h).status_code == 409


def test_lists_and_counts(client):
    h = login(client)
    a = plan(client, h).json()["id"]
    plan(client, h, eta=soon(2))
    client.post(f"/shipments/{a}/cancel", headers=h)
    everything = client.get("/shipments", headers=h).json()
    assert len(everything) == 2 and everything[0]["eta"] <= everything[1]["eta"]
    assert len(client.get("/shipments?status_in=scheduled,in_transit", headers=h).json()) == 1
    assert client.get("/shipments?dealership_code=OTHER", headers=h).json() == []
    assert client.get("/shipments/incoming-count?dealership_code=CST-04", headers=h).json() == {"count": 1}
    assert client.get("/shipments/999", headers=h).status_code == 404
    assert client.get("/shipments").status_code == 401


def test_stock_request_flow(client):
    h = login(client)
    r = client.post("/stock-requests", headers=till(client), json={"dealership_code": "cst-04", "barcode": "a1", "quantity": 5,
                                                        "note": "  low   on anvils "})
    assert r.status_code == 201, r.text
    req = r.json()
    assert (req["status"], req["note"], req["product_barcode"], req["requested_by"]) == \
        ("open", "low on anvils", "A1", "Ali · B-001")
    assert client.post("/stock-requests", headers=till(client), json={"dealership_code": "CST-04", "barcode": "A1",
                                                           "quantity": 1}).status_code == 409  # one open per product
    assert client.get("/stock-requests/open-count", headers=h).json() == {"count": 1}
    stocked(client, h)
    sid = plan(client, h, request_ids=[req["id"]]).json()["id"]
    now = client.get(f"/stock-requests/{req['id']}", headers=h).json()
    assert (now["status"], now["shipment_id"], now["shipment_status"]) == ("planned", sid, "scheduled")
    assert client.post(f"/stock-requests/{req['id']}/cancel", headers=till(client)).status_code == 409  # planned: not withdrawable
    client.post(f"/shipments/{sid}/cancel", headers=h)  # cancelling the shipment re-opens the request
    assert client.get(f"/stock-requests/{req['id']}", headers=h).json()["status"] == "open"


def test_decline_and_cancel_requests(client):
    h = login(client)
    first = client.post("/stock-requests", headers=till(client), json={"dealership_code": "CST-04", "barcode": "A1",
                                                            "quantity": 2}).json()
    r = client.post(f"/stock-requests/{first['id']}/decline", headers=h, json={"note": "none left"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["decision_note"]) == ("declined", "none left")
    assert client.post(f"/stock-requests/{first['id']}/decline", headers=h, json={}).status_code == 409
    second = client.post("/stock-requests", headers=till(client), json={"dealership_code": "CST-04", "barcode": "A1",
                                                             "quantity": 2}).json()
    assert client.post(f"/stock-requests/{second['id']}/cancel", headers=till(client)).json()["status"] == "cancelled"
    assert [r["status"] for r in client.get("/stock-requests", headers=h).json()] == ["cancelled", "declined"]
    assert len(client.get("/stock-requests?status_in=declined&limit=1", headers=h).json()) == 1
    assert client.post("/stock-requests", headers=till(client), json={"dealership_code": "NOPE", "barcode": "A1",
                                                           "quantity": 1}).status_code == 404
    assert client.post("/stock-requests", headers=till(client), json={"dealership_code": "CST-04", "barcode": "A1",
                                                           "quantity": 0}).status_code == 422
    # a till can ask but cannot decline; the depot cannot ask
    assert client.post(f"/stock-requests/{second['id']}/decline", headers=login(client, "pos"), json={}).status_code == 403
    assert client.post("/stock-requests", headers=login(client, "depot_console"),
                       json={"dealership_code": "CST-04", "barcode": "A1", "quantity": 1}).status_code == 403


def test_a_request_for_another_dealership_or_product_refuses_the_whole_plan(client):
    h = login(client)
    sql("INSERT INTO dealerships (code, name, region, city) VALUES ('IST-01', 'Metro', 'Metro', 'Istanbul')")
    sql("INSERT INTO products (barcode, name, price) VALUES ('B2', 'Bolt', 1)")
    other = client.post("/stock-requests", headers=till(client), json={"dealership_code": "IST-01", "barcode": "A1",
                                                            "quantity": 1}).json()
    assert plan(client, h, request_ids=[other["id"]]).status_code == 409
    bolt = client.post("/stock-requests", headers=till(client), json={"dealership_code": "CST-04", "barcode": "B2",
                                                           "quantity": 1}).json()
    assert plan(client, h, request_ids=[bolt["id"]]).status_code == 409  # B2 is not on the shipment
    assert client.get("/shipments", headers=h).json() == []  # nothing was kept
