"""Refunds against earlier sales, and the end-of-day till count."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("alembic")

from test_server_sales import sale, shelf  # noqa: E402,F401
from test_server_writes import PIN, _schema, client, level, login, move, sql, total  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _clean_tables(client):
    sql("TRUNCATE sale_returns, sale_return_items, day_closes, activity_events RESTART IDENTITY CASCADE")


def refund(client, h, sale_id, lines=(("A1", 1, True),), reason="Changed mind", uuid="refund-0001",
           badge="B-001", pin=PIN, **extra):
    return client.post("/returns", headers=h, json={
        "transaction_id": sale_id, "reason": reason, "client_uuid": uuid, "approver_badge": badge,
        "approver_pin": pin, "lines": [{"barcode": b, "quantity": q, "restock": r} for b, q, r in lines], **extra})


def made_sale(client, shelf, **kw):
    r = sale(client, shelf, **kw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def manager(client, admin):
    """A second person (depot manager) who can approve without it being a self-approval."""
    client.post("/employees", headers=admin, json={"badge_id": "M-1", "name": "Mo", "role": "Logistics",
                                                  "location_type": "Warehouse", "location_name": "Main"})
    r = client.post("/accounts", headers=admin, json={"badge_id": "M-1", "role": "depot_manager", "pin": "2468"})
    assert r.status_code == 201, r.text


def test_refund_restocks_and_pays_back_the_way_it_came(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    assert (level("dealership", "CST-04", "A1"), total("A1")) == (2, 2)
    r = refund(client, shelf, sid, lines=(("a1", 2, True),))
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["total"], got["payment_method"], got["dealership_code"], got["approved_by"], got["requested_by"]) == (
        10.0, "cash", "CST-04", "Ali · B-001", "Ali · B-001")
    assert [(l["barcode"], l["quantity"], l["restock"]) for l in got["lines"]] == [("A1", 2, True)]
    assert (level("dealership", "CST-04", "A1"), total("A1")) == (4, 4)
    assert sql("SELECT action, kind, target FROM admin_audit WHERE action = 'refunded'") == [("refunded", "sale", f"#{sid}")]
    assert sql("SELECT kind, severity FROM activity_events") == [("refund", "warning")]  # self-approved


def test_damaged_goods_move_only_money(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 2),))
    assert refund(client, shelf, sid, lines=(("A1", 2, False),)).status_code == 200
    assert (level("dealership", "CST-04", "A1"), total("A1")) == (3, 3)


def test_the_same_units_cannot_come_back_twice(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 2),))
    assert refund(client, shelf, sid, lines=(("A1", 2, True),)).status_code == 200
    again = refund(client, shelf, sid, uuid="refund-0002")
    assert again.status_code == 409 and "Only 0" in again.json()["detail"]
    assert refund(client, shelf, sid, lines=(("ZZ", 1, True),), uuid="refund-0003").status_code == 409
    assert refund(client, shelf, 999, uuid="refund-0004").status_code == 404


def test_a_re_sent_refund_is_stored_once(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    first = refund(client, shelf, sid).json()
    again = refund(client, shelf, sid).json()
    assert again["id"] == first["id"]
    assert sql("SELECT count(*) FROM sale_returns") == [(1,)]
    assert level("dealership", "CST-04", "A1") == 3


def test_a_manager_must_approve_with_the_right_pin(client, shelf):
    admin = login(client)
    manager(client, admin)
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    assert refund(client, shelf, sid, badge="M-1", pin="9999").status_code == 401
    assert refund(client, shelf, sid, badge="nobody").status_code == 401
    ok = refund(client, shelf, sid, badge="m-1", pin="2468")
    assert ok.status_code == 200, ok.text
    assert ok.json()["approved_by"] == "Mo · M-1"
    assert sql("SELECT severity FROM activity_events") == [("notice",)]
    events = [e[0] for e in sql("SELECT event FROM auth_events WHERE badge_id = 'M-1' ORDER BY id")]
    assert "failed" in events and events[-1] == "pin_confirmed"


def test_a_cashier_cannot_approve(client, shelf):
    admin = login(client)
    client.post("/employees", headers=admin, json={"badge_id": "C-1", "name": "Cy", "role": "Sales & service",
                                                  "location_type": "Dealership", "location_name": "Coast"})
    client.post("/accounts", headers=admin, json={"badge_id": "C-1", "role": "cashier", "pin": "2468"})
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    r = refund(client, shelf, sid, badge="C-1", pin="2468")
    assert r.status_code == 403
    assert level("dealership", "CST-04", "A1") == 2


def test_wrong_approver_pins_lock_the_account(client, shelf):
    admin = login(client)
    manager(client, admin)
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    for n in range(5):
        refund(client, shelf, sid, badge="M-1", pin="9999", uuid=f"refund-bad{n}")
    assert refund(client, shelf, sid, badge="M-1", pin="2468").status_code == 423


def test_reason_and_roles(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    assert refund(client, shelf, sid, reason="  ").status_code == 422
    assert refund(client, shelf, sid, reason="x" * 201).status_code == 422
    assert refund(client, login(client), sid).status_code == 403  # an admin token for the admin area is no till
    assert client.post("/returns", json={}).status_code == 401


def test_the_shop_removed_since_the_sale_restocks_as_unassigned(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 2),))
    sql("UPDATE transactions SET dealership_code = 'GONE'")
    assert refund(client, shelf, sid, lines=(("A1", 2, True),)).status_code == 200
    assert level("unassigned", "", "A1") == 2


def test_returnable_and_lists(client, shelf):
    sid = made_sale(client, shelf, lines=(("A1", 5, 3),))
    refund(client, shelf, sid)
    rows = client.get(f"/returns/returnable/{sid}", headers=shelf).json()
    assert [(r["barcode"], r["sold"], r["returned"]) for r in rows] == [("A1", 3, 1)]
    assert client.get("/returns/returnable/999", headers=shelf).status_code == 404
    assert [r["transaction_id"] for r in client.get("/returns", headers=shelf, params={"transaction_id": sid}).json()] == [sid]
    now = datetime.now(timezone.utc)
    window = {"since": (now - timedelta(hours=1)).isoformat(), "until": (now + timedelta(hours=1)).isoformat()}
    assert len(client.get("/returns", headers=shelf, params={**window, "dealership_code": "cst-04"}).json()) == 1
    assert client.get("/returns", headers=shelf, params={**window, "dealership_code": "other"}).json() == []
    assert client.get("/returns", headers=shelf).status_code == 422
    assert client.get(f"/sales/{sid}", headers=shelf).json()["id"] == sid
    assert client.get("/sales/999", headers=shelf).status_code == 404


def test_sales_are_read_net_of_refunds(client, shelf):
    one = made_sale(client, shelf, lines=(("A1", 5, 2),))
    two = made_sale(client, shelf, uuid="sale-0002", lines=(("A1", 5, 1),))
    refund(client, shelf, one, lines=(("A1", 1, True),))
    refund(client, shelf, two, lines=(("A1", 1, True),), uuid="refund-0002")
    now = datetime.now(timezone.utc)
    window = {"since": (now - timedelta(hours=1)).isoformat(), "until": (now + timedelta(hours=1)).isoformat(),
              "dealership_code": "CST-04"}
    net = client.get("/sales", headers=shelf, params=window).json()
    assert [(s["id"], s["total"], [l["quantity"] for l in s["lines"]]) for s in net] == [(one, 5.0, [1])]  # two is gone
    gross = client.get("/sales", headers=shelf, params={**window, "net": "false"}).json()
    assert sorted((s["id"], s["total"]) for s in gross) == [(one, 10.0), (two, 5.0)]


# --- the day count --------------------------------------------------------------------------

def day():
    now = datetime.now(timezone.utc)
    return {"start": (now - timedelta(hours=2)).isoformat(), "end": (now + timedelta(hours=2)).isoformat(),
            "business_date": now.date().isoformat()}


def test_day_summary_adds_up_sales_and_refunds(client, shelf):
    made_sale(client, shelf, lines=(("A1", 5, 3),))
    other = made_sale(client, shelf, uuid="sale-0002", lines=(("A1", 5, 1),))
    sql("UPDATE transactions SET payment_method = 'card' WHERE id = %s", (other,))
    refund(client, shelf, 1, lines=(("A1", 1, True),))
    window = {k: v for k, v in day().items() if k != "business_date"}
    got = client.get("/day-close/summary", headers=shelf, params={**window, "dealership_code": "cst-04"}).json()
    assert got == {"dealership_code": "CST-04", "sales_count": 2, "cash_sales": 15.0, "card_sales": 5.0,
                   "other_sales": 0.0, "cash_refunds": 5.0, "card_refunds": 0.0, "refunds_count": 1}
    empty = client.get("/day-close/summary", headers=shelf, params=window).json()  # the no-shop till
    assert empty["sales_count"] == 0


def test_closing_the_day_stores_the_difference(client, shelf):
    made_sale(client, shelf, lines=(("A1", 5, 3),))
    refund(client, shelf, 1, lines=(("A1", 1, True),))
    r = client.post("/day-close", headers=shelf, json={**day(), "dealership_code": "CST-04",
                                                        "counted_cash": 8.5, "note": " short "})
    assert r.status_code == 201, r.text
    got = r.json()
    assert (got["expected_cash"], got["counted_cash"], got["difference"], got["note"], got["closed_by"]) == (
        10.0, 8.5, -1.5, "short", "Ali · B-001")
    assert sql("SELECT kind, severity FROM activity_events WHERE kind = 'day_close'") == [("day_close", "warning")]
    latest = client.get("/day-closes/latest", headers=shelf, params={"business_date": day()["business_date"],
                                                                      "dealership_code": "CST-04"}).json()
    assert latest["id"] == got["id"]
    assert client.get("/day-closes/latest", headers=shelf, params={"business_date": "2001-01-01"}).json() is None


def test_day_closes_list_and_filter(client, shelf):
    made_sale(client, shelf, lines=(("A1", 5, 1),))
    client.post("/day-close", headers=shelf, json={**day(), "dealership_code": "CST-04", "counted_cash": 5})
    client.post("/day-close", headers=shelf, json={**day(), "dealership_code": "CST-04", "counted_cash": 4})
    both = client.get("/day-closes", headers=shelf).json()
    assert [c["difference"] for c in both] == [-1.0, 0.0]  # newest first
    assert [c["difference"] for c in client.get("/day-closes", headers=shelf, params={"only_differences": "true"}).json()] == [-1.0]
    assert client.get("/day-closes", headers=shelf, params={"dealership_code": "zzz"}).json() == []


@pytest.mark.parametrize("bad", [{"counted_cash": -1}, {"counted_cash": 2e9}, {"note": "x" * 201}])
def test_day_close_validates(client, shelf, bad):
    body = {**day(), "dealership_code": "CST-04", "counted_cash": 1, **bad}
    assert client.post("/day-close", headers=shelf, json=body).status_code == 422


def test_only_a_till_closes_the_day(client, shelf):
    body = {**day(), "dealership_code": "CST-04", "counted_cash": 1}
    assert client.post("/day-close", headers=login(client), json=body).status_code == 403
    assert client.post("/day-close", json=body).status_code == 401
