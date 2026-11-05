"""Transfers, counts, unplaced stock, reorder levels and the stock reads."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import _schema, client, level, login, move, sql, total  # noqa: E402,F401


def tr(client, h, legs, source=("warehouse", "WH-01"), barcode="A1", **extra):
    return client.post("/stock/transfers", headers=h, json={
        "barcode": barcode, "source": {"kind": source[0], "code": source[1]},
        "legs": [{"kind": k, "code": c, "quantity": q} for k, c, q in legs], **extra})


def test_transfer_and_distribute_keep_the_total(client):
    h = login(client)
    move(client, h, "receive", 8)
    r = tr(client, h, [("warehouse", "WH-02", 3), ("dealership", "cst-04", 2)], note="rebalance")
    assert r.status_code == 201, r.text
    assert r.json() == {"moved": 5, "source_level_after": 3}
    assert (level("warehouse", "WH-02", "A1"), level("dealership", "CST-04", "A1"), total("A1")) == (3, 2, 8)
    reasons = sql("SELECT reason, movement_type, reference FROM stock_movements WHERE reason = 'transfer' "
                  "ORDER BY id")
    assert reasons[:2] == [("transfer", "dispatch", "WH-02"), ("transfer", "receive", "WH-01")]


def test_transfer_is_all_or_nothing(client):
    h = login(client)
    move(client, h, "receive", 4)
    assert tr(client, h, [("warehouse", "WH-02", 3), ("dealership", "CST-04", 3)]).status_code == 409
    assert (level("warehouse", "WH-01", "A1"), level("warehouse", "WH-02", "A1")) == (4, 0)
    assert tr(client, h, [("warehouse", "WH-01", 1)]).status_code == 422  # same place
    assert tr(client, h, [("warehouse", "WH-02", 11)], source=("warehouse", "WH-02")).status_code == 422


def test_transfer_respects_capacity(client):
    h = login(client)
    move(client, h, "receive", 20, code="WH-02")
    assert tr(client, h, [("warehouse", "WH-01", 11)], source=("warehouse", "WH-02")).status_code == 409


def test_count_moves_total_and_absorbs_unplaced(client):
    h = login(client)
    client.post("/products", headers=h, json={"barcode": "U1", "name": "Unplaced", "price": 1, "stock_quantity": 5})
    r = client.post("/stock/counts", headers=h, json={"barcode": "u1", "place": {"kind": "warehouse", "code": "WH-02"},
                                                     "counted": 7})
    assert r.json() == {"difference": 7}
    # 5 were the unplaced ones, 2 were found: total 7, nothing left unplaced
    assert (level("warehouse", "WH-02", "U1"), level("unassigned", "", "U1"), total("U1")) == (7, 0, 7)
    r = client.post("/stock/counts", headers=h, json={"barcode": "U1", "place": {"kind": "warehouse", "code": "WH-02"},
                                                     "counted": 4})
    assert r.json() == {"difference": -3} and total("U1") == 4


def test_unplaced_place_all_and_clear(client):
    h = login(client)
    client.post("/products", headers=h, json={"barcode": "U1", "name": "U", "price": 1, "stock_quantity": 3})
    client.post("/products", headers=h, json={"barcode": "U2", "name": "V", "price": 1, "stock_quantity": 2})
    assert client.post("/stock/unassigned/place", headers=h, json={"kind": "warehouse", "code": "WH-02"}).json() == \
        {"moved": 5}
    assert (level("warehouse", "WH-02", "U1"), total("U1")) == (3, 3)
    client.post("/products", headers=h, json={"barcode": "U3", "name": "W", "price": 1, "stock_quantity": 4})
    assert client.post("/stock/unassigned/clear", headers=h).json() == {"removed": 4}
    assert total("U3") == 0


def test_reorder_levels_and_products_at(client):
    h = login(client)
    move(client, h, "receive", 2, kind="dealership", code="CST-04")
    put = {"place": {"kind": "dealership", "code": "CST-04"}, "barcode": "a1", "level": 5}
    assert client.put("/stock/reorder-levels", headers=h, json=put).status_code == 200
    assert client.get("/stock/reorder-levels?kind=dealership&code=CST-04", headers=h).json() == {"A1": 5}
    [here] = client.get("/stock/products-at?kind=dealership&code=CST-04", headers=h).json()
    assert (here["quantity"], here["critical_stock_level"], here["stocked_here"]) == (2, 5, True)
    [there] = client.get("/stock/products-at?kind=warehouse&code=WH-02", headers=h).json()
    assert (there["quantity"], there["stocked_here"]) == (0, False)
    client.put("/stock/reorder-levels", headers=h, json={**put, "level": None})
    assert client.get("/stock/reorder-levels?kind=dealership&code=CST-04", headers=h).json() == {}


def test_reads_for_the_screens(client):
    h = login(client)
    move(client, h, "receive", 6)
    move(client, h, "dispatch", 1)
    assert client.get("/stock/units-by-location", headers=h).json() == [
        {"location_kind": "warehouse", "location_code": "WH-01", "units": 5}]
    assert client.get("/stock/in-transit", headers=h).json() == {"units": 0}
    totals = client.get("/stock/movement-totals?since=2000-01-01T00:00:00Z", headers=h).json()
    assert totals == [{"location_kind": "warehouse", "location_code": "WH-01", "units_in": 6, "units_out": 1}]
    assert list(client.get("/stock/last-moved?kind=warehouse&code=WH-01", headers=h).json()) == ["A1"]
    out = client.get("/stock-movements?location_kind=warehouse&location_code=wh-01&movement_type=receive",
                     headers=h).json()
    assert [(m["quantity"], m["product_name"], m["reason"]) for m in out] == [(6, "Anvil", "receive")]
    assert client.get("/stock-movements?until=2000-01-01T00:00:00Z", headers=h).json() == []
    assert client.get("/stock-levels", headers=h).json()[0]["product_name"] == "Anvil"


def test_who_may_do_what(client):
    sql("UPDATE accounts SET role = 'depot_manager'")
    h = login(client, "depot_console")
    assert tr(client, h, [("warehouse", "WH-02", 1)]).status_code == 403  # transfers: Admin only
    move(client, h, "receive", 3)
    assert client.post("/stock/counts", headers=h, json={"barcode": "A1", "place": {"kind": "warehouse",
                                                         "code": "WH-01"}, "counted": 2}).status_code == 200
    assert client.post("/stock/counts", headers=h, json={"barcode": "A1", "place": {"kind": "dealership",
                                                         "code": "CST-04"}, "counted": 2}).status_code == 403
    sql("UPDATE accounts SET role = 'cashier'")
    pos = login(client, "pos")
    assert client.get("/stock/products-at?kind=dealership&code=CST-04", headers=pos).status_code == 200
    assert client.put("/stock/reorder-levels", headers=pos, json={
        "place": {"kind": "dealership", "code": "CST-04"}, "barcode": "A1", "level": 1}).status_code == 403


def bell(*kinds):
    """(kind, severity, source, place, actor) of the stock alerts so far, oldest first."""
    return sql("SELECT kind, severity, source, location_code, actor FROM activity_events "
               "WHERE kind = ANY(%s) ORDER BY id", (list(kinds or ("stock_low", "stock_out")),))


def test_stock_leaving_a_place_rings_the_bell_once_per_crossing(client):
    h = login(client)
    sql("TRUNCATE activity_events")
    client.patch("/products/A1", headers=h, json={"critical_stock_level": 3})
    move(client, h, "receive", 8)
    assert move(client, h, "dispatch", 3).status_code == 201             # 8 -> 5, still above 3
    assert bell() == []
    assert move(client, h, "dispatch", 2).status_code == 201             # 5 -> 3 crosses the level
    assert bell() == [("stock_low", "warning", "depot", "WH-01", "Ali · B-001")]
    assert move(client, h, "dispatch", 1).status_code == 201             # 3 -> 2: already low, no repeat
    assert len(bell()) == 1
    assert move(client, h, "dispatch", 2).status_code == 201             # 2 -> 0
    assert [b[:2] for b in bell()] == [("stock_low", "warning"), ("stock_out", "critical")]
    assert move(client, h, "receive", 5).status_code == 201              # receiving never alerts
    assert len(bell()) == 2
    sql("TRUNCATE activity_events")
    client.put("/stock/reorder-levels", headers=h, json={"place": {"kind": "warehouse", "code": "WH-01"},
                                                         "barcode": "A1", "level": 0})
    assert move(client, h, "dispatch", 5).status_code == 201             # level 0 here = never "low", still "out"
    assert [b[0] for b in bell()] == ["stock_out"]


def test_transfers_counts_and_the_till_ring_it_too(client):
    h = login(client)
    sql("TRUNCATE activity_events")
    client.patch("/products/A1", headers=h, json={"critical_stock_level": 3})
    move(client, h, "receive", 6)
    tr(client, h, [("warehouse", "WH-02", 3)])                            # the source falls 6 -> 3
    assert [(b[0], b[3]) for b in bell()] == [("stock_low", "WH-01")]
    count = lambda n: client.post("/stock/counts", headers=h, json={
        "barcode": "A1", "place": {"kind": "warehouse", "code": "WH-02"}, "counted": n})
    count(6)                                                             # counting UP never alerts
    assert len(bell()) == 1
    count(2)                                                             # a count down 6 -> 2 crosses level 3
    assert [(b[0], b[3]) for b in bell()] == [("stock_low", "WH-01"), ("stock_low", "WH-02")]
    move(client, h, "receive", 4, kind="dealership", code="CST-04")
    pos = login(client, "pos")
    sold = client.post("/sales", headers=pos, json={
        "client_uuid": "bell-0001", "dealership_code": "CST-04", "payment_method": "cash",
        "lines": [{"barcode": "A1", "name": "", "unit_price": 5, "quantity": 4}]})
    assert sold.status_code == 200, sold.text
    assert bell("stock_out")[0][:4] == ("stock_out", "critical", "pos", "CST-04")
    [sale_row] = sql("SELECT source, actor, location_code, data_json->>'units' FROM activity_events "
                     "WHERE kind = 'sale'")
    assert sale_row == ("pos", "Ali · B-001", "CST-04", "4")
    # a sale replayed after an outage is stored without a fresh "sale" line in the feed
    late = client.post("/sales", headers=pos, json={
        "client_uuid": "bell-0002", "dealership_code": "CST-04", "payment_method": "cash",
        "sold_at": "2026-11-01T09:30:00Z", "lines": []})
    assert late.status_code in (200, 422)
    assert sql("SELECT count(*) FROM activity_events WHERE kind = 'sale'") == [(1,)]
