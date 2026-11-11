"""One walk through the whole business, step by step, as the people who do it.

Admin (root) opens a dealership and a depot and names their managers; the depot buys a new product above
its price band, Admin approves, the Floor books the delivery by barcode, the depot ships some to the shop,
the shop finds two missing, sells three for cash and three by card; the depot records a cheque and a
receivable; each manager hires their own people and punches them in and out. Admin watches all of it in
the live activity feed and the lists, but hires nobody itself.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("alembic")

from test_server_writes import PIN, _schema, client, level, sql, total  # noqa: E402,F401

NID = iter(["10011111152", "10022222236", "10033333310", "10044444494",   # valid T.C. Kimlik numbers
            "10055555578", "10066666652", "10077777736", "10088888810"])


@pytest.fixture(autouse=True)
def _clean(client):
    sql("TRUNCATE purchase_orders, purchase_price_ranges, activity_events, shipments, shipment_lines, "
        "stock_requests, transactions, transaction_items, ledger_entries, ledger_audit, attendance_records "
        "RESTART IDENTITY CASCADE")


def sign_in(client, badge, pin, area):
    r = client.post("/auth/login", json={"badge_id": badge, "pin": pin, "area": area})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def kinds(client, admin):
    return [e["kind"] for e in client.get("/activity/recent?limit=200", headers=admin).json()]


def ok(r, code=200):
    assert r.status_code == code, r.text
    return r.json()


def test_the_whole_scenario(client):
    root = sign_in(client, "B-001", PIN, "admin")

    # (11, 12) Admin opens a dealership and a depot and names their managers; nobody else.
    ok(client.post("/dealerships", headers=root, json={"code": "IST-01", "name": "Kadikoy", "region": "Metro",
                                                     "city": "Istanbul", "manager_name": "Ayse"}), 201)
    ok(client.post("/warehouses", headers=root, json={"code": "WH-09", "name": "Gebze", "city": "Kocaeli"}), 201)
    ok(client.post("/accounts", headers=root, json={"badge_id": "D-1", "role": "depot_manager", "pin": "246802",
                                                    "name": "Mert", "location_name": "WH-09"}), 201)
    ok(client.post("/accounts", headers=root, json={"badge_id": "S-1", "role": "cashier", "pin": "246802",
                                                    "name": "Ayse", "location_name": "IST-01"}), 201)
    depot = sign_in(client, "D-1", "246802", "depot_console")
    shop = sign_in(client, "S-1", "246802", "pos")

    # (1) A new product from the Admin app; its barcode is what the depot and the till scan.
    ok(client.post("/products", headers=root, json={"barcode": "TV-55", "name": "TV 55", "price": 25000,
                                                    "critical_stock_level": 2}), 201)

    # (2) Admin sets the purchase price band.
    ok(client.put("/price-ranges/TV-55", headers=root, json={"min_unit_price": 14000, "max_unit_price": 16000}))

    # (3) The depot manager buys ABOVE the band: the order is held for Admin.
    po = ok(client.post("/purchase-orders", headers=depot, json={
        "barcode": "TV-55", "supplier": "Vestel", "quantity": 20, "unit_price": 17500, "site": "WH-09"}), 201)
    assert (po["status"], po["hold_reason"]) == ("pending", "above_range")
    assert "po_pending" in kinds(client, root)
    assert client.post(f"/purchase-orders/{po['id']}/approve", headers=depot, json={}).status_code == 403

    # (4) Admin approves.
    assert ok(client.post(f"/purchase-orders/{po['id']}/approve", headers=root, json={}))["status"] == "sent"

    # (5) The goods arrive: the Floor books them by barcode, which settles the order; the units are in the depot.
    got = ok(client.post("/purchase-orders/receive-delivery", headers=depot, json={
        "warehouse_code": "WH-09", "barcode": "tv-55", "quantity": 20, "reference": "IRS-778"}))
    assert got["orders"] == [f"PO-{po['id']:04d}"] or len(got["orders"]) == 1
    assert ok(client.get(f"/purchase-orders/{po['id']}", headers=root))["status"] == "received"
    assert (level("warehouse", "WH-09", "TV-55"), total("TV-55")) == (20, 20)
    assert "stock_in" in kinds(client, root)  # Admin sees the goods come in

    # (6) The depot manager sees the shop has none and sends 20.
    at_shop = ok(client.get("/stock/products-at?kind=dealership&code=IST-01", headers=depot))
    assert next(p for p in at_shop if p["barcode"] == "TV-55")["quantity"] == 0
    eta = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    sh = ok(client.post("/shipments", headers=depot, json={
        "origin": "WH-09 · Gebze", "origin_code": "WH-09", "dealership_code": "IST-01", "carrier": "Aras",
        "eta": eta, "lines": [{"barcode": "TV-55", "quantity": 20}]}), 201)
    ok(client.post(f"/shipments/{sh['id']}/dispatch", headers=depot))

    # (7) The shop counts 18: two missing, reported with the receipt.
    rec = ok(client.post(f"/shipments/{sh['id']}/receive", headers=shop,
                         json={"received": {"TV-55": 18}, "note": "2 kutu eksik"}))
    assert rec["status"] == "delivered" and rec["lines"][0]["received_qty"] == 18
    assert level("dealership", "IST-01", "TV-55") == 18 and total("TV-55") == 18
    short = next(e for e in client.get("/activity/recent?limit=200", headers=root).json()
                 if e["kind"] == "shipment_short")
    assert short["severity"] == "warning" and short["data"]["missing"] == 2

    # (8) Three sold for cash, three by card; (9) every sale reaches Admin.
    for i, method in enumerate(["cash"] * 3 + ["card"] * 3):
        ok(client.post("/sales", headers=shop, json={
            "client_uuid": f"scenario-sale-{i}", "dealership_code": "IST-01", "payment_method": method,
            "lines": [{"barcode": "TV-55", "name": "TV 55", "unit_price": 25000, "quantity": 1}]}))
    day = {"since": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
           "until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
    sales = ok(client.get("/sales", headers=root, params=day))
    assert sorted(s["payment_method"] for s in sales) == ["card"] * 3 + ["cash"] * 3
    assert sum(s["total"] for s in sales) == 150000
    assert level("dealership", "IST-01", "TV-55") == 12
    assert kinds(client, root).count("sale") == 6

    # (10) The depot records a 20,000 TL cheque it wrote and a 50,000 TL receivable; Admin sees both.
    today = date.today()
    ok(client.post("/ledger", headers=depot, json={
        "direction": "out", "doc_type": "check", "doc_no": "CEK-001", "counterparty": "Vestel", "site": "WH-09",
        "issue_date": str(today), "due_date": str(today + timedelta(days=30)), "amount": 20000}), 201)
    ok(client.post("/ledger", headers=depot, json={
        "direction": "in", "doc_type": "invoice", "doc_no": "FTR-050", "counterparty": "Bayi Kadikoy",
        "site": "WH-09", "issue_date": str(today), "due_date": str(today + timedelta(days=60)), "amount": 50000}), 201)
    books = {(e["direction"], e["doc_type"], e["amount"]) for e in ok(client.get("/ledger", headers=root))}
    assert books == {("out", "check", 20000.0), ("in", "invoice", 50000.0)}
    assert kinds(client, root).count("ledger_entry") == 2

    # (11) The shop manager hires four, two of them to work the till.
    for badge, job in (("S-2", "Sales & service"), ("S-3", "Sales & service"), ("S-4", "Operations"),
                       ("S-5", "Operations")):
        ok(client.post("/employees", headers=shop, json={
            "badge_id": badge, "name": f"Kisi {badge}", "role": job, "location_type": "Dealership",
            "location_name": "IST-01", "national_id": next(NID)}), 201)
    # ...and gives the two who work the till their own PIN (the shop manager's job is Management).
    sql("UPDATE employees SET role = 'Management' WHERE badge_id = 'S-1'")
    for badge in ("S-2", "S-3"):
        assert ok(client.post("/accounts/till", headers=shop, json={"badge_id": badge, "pin": "583920"}),
                  201)["role"] == "cashier"
    sign_in(client, "S-2", "583920", "pos")
    assert client.post("/accounts/till", headers=shop, json={"badge_id": "S-2", "pin": "583920"}).status_code == 409
    assert client.post("/accounts/till", headers=shop, json={"badge_id": "D-1", "pin": "583920"}).status_code == 403
    clerk = sign_in(client, "S-2", "583920", "pos")   # a plain cashier gives nobody a PIN
    assert client.post("/accounts/till", headers=clerk, json={"badge_id": "S-4", "pin": "583920"}).status_code == 403
    assert kinds(client, root).count("till_account_added") == 2

    # (12) The depot manager hires three; Admin cannot hire, only watch.
    for badge in ("D-2", "D-3", "D-4"):
        ok(client.post("/employees", headers=depot, json={
            "badge_id": badge, "name": f"Kisi {badge}", "role": "Operations", "location_type": "Warehouse",
            "location_name": "WH-09", "national_id": next(NID)}), 201)
    assert client.post("/employees", headers=depot, json={
        "badge_id": "X-1", "name": "X", "role": "Operations", "location_type": "Dealership",
        "location_name": "IST-01", "national_id": next(NID)}).status_code == 403   # not the depot's own place
    watched = {e["badge_id"] for e in ok(client.get("/employees", headers=root))}
    assert {"S-2", "S-3", "S-4", "S-5", "D-2", "D-3", "D-4"} <= watched
    assert kinds(client, root).count("employee_added") == 7

    # (13) Each manager punches their own people in and out; Admin sees the records and is told.
    for headers, badges in ((shop, ("S-2", "S-3")), (depot, ("D-2",))):
        for badge in badges:
            ok(client.post("/attendance/check-in", headers=headers, json={"badge_id": badge}))
    assert client.post("/attendance/check-in", headers=shop, json={"badge_id": "D-3"}).status_code == 409
    ok(client.post("/attendance/check-out", headers=shop, json={"badge_id": "S-2"}))
    start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    seen = client.get("/attendance/records", headers=root, params={"start": start, "end": end})
    assert {r["badge_id"] for r in ok(seen)} == {"S-2", "S-3", "D-2"}
    feed = kinds(client, root)
    assert feed.count("staff_check_in") == 3 and feed.count("staff_check_out") == 1
