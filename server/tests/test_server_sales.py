"""POST /sales and GET /sales."""

import pytest

pytest.importorskip("alembic")

from test_server_writes import _schema, client, level, login, move, sql, total  # noqa: E402,F401


def sale(client, h, uuid="sale-0001", lines=(("A1", 5, 2),), shop="CST-04", **extra):
    return client.post("/sales", headers=h, json={
        "client_uuid": uuid, "dealership_code": shop, "payment_method": "cash",
        "lines": [{"barcode": b, "name": "", "unit_price": p, "quantity": q} for b, p, q in lines], **extra})


@pytest.fixture()
def shelf(client):
    sql("TRUNCATE transactions RESTART IDENTITY CASCADE")
    h = login(client)
    move(client, h, "receive", 5, kind="dealership", code="CST-04")
    client.patch("/products/A1", headers=h, json={"cost_price": 3})
    return login(client, "pos")


def test_a_sale_takes_units_off_the_shelf_and_total(client, shelf):
    r = sale(client, shelf, lines=(("a1", 5, 2), ("A1", 5, 1)))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total"], body["dealership_code"], body["cashier"], body["duplicate"]) == (15.0, "CST-04",
                                                                                             "Ali · B-001", False)
    assert [(ln["barcode"], ln["name"], ln["unit_cost"], ln["cost_known"]) for ln in body["lines"]] == \
        [("A1", "Anvil", 3.0, True)] * 2
    assert (level("dealership", "CST-04", "A1"), total("A1")) == (2, 2)


def test_the_same_sale_twice_is_stored_once(client, shelf):
    first = sale(client, shelf).json()
    again = sale(client, shelf).json()
    assert again["id"] == first["id"] and again["duplicate"] is True
    assert level("dealership", "CST-04", "A1") == 3
    assert len(sql("SELECT 1 FROM transactions")) == 1


def test_live_sale_refused_when_price_moved_or_shelf_short(client, shelf):
    r = sale(client, shelf, lines=(("A1", 4.99, 1),))
    assert r.status_code == 409 and "Prices changed" in r.json()["detail"]
    r = sale(client, shelf, uuid="sale-0002", lines=(("A1", 5, 6),))
    assert r.status_code == 409 and "Only 5 units" in r.json()["detail"]
    assert sql("SELECT count(*) FROM transactions")[0][0] == 0
    assert level("dealership", "CST-04", "A1") == 5


def test_offline_sale_keeps_the_price_paid_and_its_time(client, shelf):
    r = sale(client, shelf, lines=(("A1", 4.5, 1),), sold_at="2026-11-01T09:30:00+03:00")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 4.5 and r.json()["created_at"].startswith("2026-11-01T06:30:00")
    short = sale(client, shelf, uuid="sale-0003", lines=(("A1", 5, 9),), sold_at="2026-11-01T09:31:00Z")
    assert short.status_code == 409  # kept aside on the till for a person to look at


def test_who_may_sell(client, shelf):
    admin_in_admin = login(client)
    assert sale(client, admin_in_admin).status_code == 403  # a sale comes from a till (pos area)
    sql("UPDATE accounts SET role = 'depot_manager'")
    assert sale(client, shelf).status_code == 401  # role changed since sign-in: sign in again
    sql("UPDATE accounts SET role = 'cashier'")
    till = login(client, "pos")
    assert sale(client, till, uuid="sale-0009").status_code == 403  # a cashier whose badge has no dealership sells nowhere
    sql("UPDATE employees SET location_type = 'Dealership', location_name = 'Coast'")
    assert sale(client, till, uuid="sale-0009").status_code == 200
    sql("UPDATE accounts SET role = 'depot_manager'")
    assert sale(client, login(client, "depot_console"), uuid="sale-0010").status_code == 403


def test_list_sales(client, shelf):
    sale(client, shelf)
    sale(client, shelf, uuid="sale-0002", lines=(("A1", 5, 1),))
    h = login(client)
    rows = client.get("/sales?since=2000-01-01T00:00:00Z&until=2100-01-01T00:00:00Z&dealership_code=cst-04",
                      headers=h).json()
    assert [r["client_uuid"] for r in rows] == ["sale-0002", "sale-0001"]
    sql("UPDATE accounts SET role = 'cashier'")
    pos = login(client, "pos")
    assert client.get("/sales?since=2000-01-01T00:00:00Z&until=2100-01-01T00:00:00Z", headers=pos).status_code == 403
