"""Red-team tests: attacks on the running API, written as tests so they stay fixed.

Everything here runs against a real PostgreSQL through the real app. The groups:

* every endpoint refuses a request with no token, a forged token or an expired one;
* a signed-in person whose account was switched off or demoted loses access at once (reads too);
* a cashier is tied to their own dealership (no selling from, reading or refunding another shop);
* a cashier or depot manager cannot reach administrator-only actions, however the request is built;
* injection payloads, NUL bytes, oversize and odd values never cause a server error or change data;
* many guesses from one address are slowed down, big bodies are refused, nothing leaks in headers;
* simultaneous requests cannot sell the last unit twice or refund the same units twice.
"""

import re
import threading
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

import jwt
import pytest

pytest.importorskip("alembic")

from fastapi.testclient import TestClient  # noqa: E402

from gpusa_server import create_admin  # noqa: E402,F401
from gpusa_server.config import Settings  # noqa: E402
from gpusa_server.main import create_app  # noqa: E402
from test_server_writes import PIN, URL, _schema, client, login, move, sql  # noqa: E402,F401

SECRET = "x" * 40
CASHIER_PIN = "246802"

# Open on purpose: nothing secret is behind these (health, sign-in itself, the recovery/set-up doors that
# have their own guards and tests, the public store settings).
PUBLIC = {("get", "/health"), ("get", "/health/db"), ("post", "/auth/login"), ("get", "/setup/status"),
          ("post", "/setup/first-admin"), ("get", "/recovery/status"), ("post", "/recovery/reset"),
          ("get", "/settings"), ("get", "/changes"), ("get", "/openapi.json"), ("get", "/docs"),
          ("get", "/docs/oauth2-redirect"), ("get", "/redoc")}


@pytest.fixture()
def world(client):
    """Admin (B-001), a cashier at CST-04 (C-1), a cashier at WST-01 (C-2), a depot manager (M-1)."""
    sql("TRUNCATE transactions, sale_returns, day_closes, activity_events RESTART IDENTITY CASCADE")
    sql("TRUNCATE ledger_entries, ledger_audit, purchase_orders, shipments, stock_requests RESTART IDENTITY CASCADE")
    admin = login(client)
    sql("INSERT INTO dealerships (code, name, region, city) VALUES ('WST-01', 'West', 'Coastal', 'Bursa')")
    people = [("C-1", "Cass", "Sales & service", "Dealership", "CST-04", "cashier"),
              ("C-2", "Cory", "Sales & service", "Dealership", "WST-01", "cashier"),
              ("M-1", "Mo", "Logistics", "Warehouse", "WH-01", "depot_manager"),
              ("M-2", "Mia", "Logistics", "Warehouse", "WH-02", "depot_manager")]
    for badge, name, role, kind, place, account_role in people:
        r = client.post("/employees", headers=admin, json={"badge_id": badge, "name": name, "role": role,
                                                           "location_type": kind, "location_name": place})
        assert r.status_code == 201, r.text
        r = client.post("/accounts", headers=admin, json={"badge_id": badge, "role": account_role,
                                                          "pin": CASHIER_PIN})
        assert r.status_code == 201, r.text
    move(client, admin, "receive", 5, kind="dealership", code="CST-04")
    move(client, admin, "receive", 5, kind="dealership", code="WST-01")

    def sign_in(badge, area):
        r = client.post("/auth/login", json={"badge_id": badge, "pin": CASHIER_PIN, "area": area})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    return {"admin": admin, "till": login(client, "pos"), "cass": sign_in("C-1", "pos"),
            "cory": sign_in("C-2", "pos"), "mo": sign_in("M-1", "depot_console"),
            "mia": sign_in("M-2", "depot_console"), "sign_in": sign_in}


def sale_body(shop, uuid, price=5, qty=1, **extra):
    return {"client_uuid": uuid, "dealership_code": shop, "payment_method": "cash",
            "lines": [{"barcode": "A1", "unit_price": price, "quantity": qty}], **extra}


# --- 1. tokens ------------------------------------------------------------------------------

def _operations(client):
    for path, ops in sorted(client.app.openapi()["paths"].items()):
        for method, op in ops.items():
            if method in ("get", "post", "put", "patch", "delete"):
                yield method, path, op


def _url(path):
    return re.sub(r"\{[^}]*\}", "1", path)


def test_every_endpoint_but_the_public_ones_refuses_a_request_without_a_token(client):
    seen = 0
    for method, path, _op in _operations(client):
        if (method, path) in PUBLIC:
            continue
        seen += 1
        r = client.request(method.upper(), _url(path), json={} if method != "get" else None)
        assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code} without a token"
    assert seen > 80  # the walk really covered the API


def test_forged_expired_and_odd_tokens_are_refused(client):
    now = datetime.now(timezone.utc)
    claims = {"sub": "1", "badge": "B-001", "name": "Ali", "role": "admin", "area": "admin",
              "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=1)).timestamp())}
    forged = {
        "wrong secret": jwt.encode(claims, "y" * 40, algorithm="HS256"),
        "expired": jwt.encode({**claims, "exp": int((now - timedelta(minutes=1)).timestamp())}, SECRET,
                              algorithm="HS256"),
        "no expiry": jwt.encode({k: v for k, v in claims.items() if k != "exp"}, SECRET, algorithm="HS256"),
        "alg none": jwt.encode(claims, None, algorithm="none"),
        "other algorithm": jwt.encode(claims, SECRET, algorithm="HS512"),
        "garbage": "not.a.token",
        "empty": "",
        "three dots": "a.b.c",
    }
    for name, token in forged.items():
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, f"{name} token was accepted: {r.status_code}"
    for header in ("Basic YWxpOnBpbg==", "Bearer", "bearer", "Token abc", "Bearer  ", "Bearer a b"):
        assert client.get("/auth/me", headers={"Authorization": header}).status_code == 401, header


def test_a_token_whose_claims_were_edited_is_refused(client):
    token = login(client)["Authorization"].split()[1]
    head, body, sig = token.split(".")
    import base64
    import json
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    claims["role"] = "admin"
    claims["sub"] = "999"
    edited = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {head}.{edited}.{sig}"}).status_code == 401


def test_a_switched_off_or_demoted_account_loses_access_on_its_very_next_request(world, client):
    admin, cass = world["admin"], world["cass"]
    assert client.get("/products", headers=cass).status_code == 200
    assert client.post("/accounts/C-1/active", headers=admin, json={"active": False}).status_code == 204
    assert client.get("/products", headers=cass).status_code == 401      # reads too, not only writes
    assert client.get("/auth/me", headers=cass).status_code == 401
    assert client.post("/accounts/C-1/active", headers=admin, json={"active": True}).status_code == 204
    # demoted: the token still says "cashier"/"pos"; the account now is a depot manager
    cass2 = world["sign_in"]("C-1", "pos")
    assert client.post("/accounts/C-1/role", headers=admin, json={"role": "depot_manager"}).status_code == 422
    sql("UPDATE employees SET location_type = 'Warehouse', location_name = 'WH-01' WHERE badge_id = 'C-1'")
    assert client.post("/accounts/C-1/role", headers=admin, json={"role": "depot_manager"}).status_code == 204
    assert client.post("/sales", headers=cass2, json=sale_body("CST-04", "demoted-0001")).status_code in (401, 403)
    assert client.get("/sales", headers=cass2, params={"since": "2020-01-01T00:00:00Z",
                                                       "until": "2030-01-01T00:00:00Z",
                                                       "dealership_code": "CST-04"}).status_code == 401


def test_changing_a_pin_signs_that_person_out_everywhere(world, client):
    admin, cass = world["admin"], world["cass"]
    assert client.get("/products", headers=cass).status_code == 200
    assert client.post("/accounts/C-1/pin", headers=admin, json={"pin": "135702"}).status_code == 204
    assert client.get("/products", headers=cass).status_code == 401      # the old token stopped working
    r = client.post("/auth/login", json={"badge_id": "C-1", "pin": "135702", "area": "pos"})
    assert r.status_code == 200
    assert client.get("/products", headers={"Authorization": "Bearer " + r.json()["token"]}).status_code == 200


def test_a_deleted_employee_cannot_keep_using_an_old_token(world, client):
    admin, cass = world["admin"], world["cass"]
    assert client.delete("/employees/C-1", headers=admin).status_code in (204, 409)
    sql("UPDATE employees SET is_active = FALSE WHERE badge_id = 'C-1'")
    assert client.get("/products", headers=cass).status_code == 401


# --- 2. one cashier, one shop ----------------------------------------------------------------

def test_a_cashier_cannot_sell_from_another_shop(world, client):
    r = client.post("/sales", headers=world["cass"], json=sale_body("WST-01", "cross-sale-0001"))
    assert r.status_code == 403, r.text
    assert sql("SELECT quantity FROM stock_levels WHERE location_code = 'WST-01'") == [(5,)]   # untouched
    assert client.post("/sales", headers=world["cass"], json=sale_body("CST-04", "own-sale-00001")).status_code == 200
    # no shop at all would sell from the shared "unassigned" pool: not for a cashier
    assert client.post("/sales", headers=world["cass"], json=sale_body(None, "no-shop-sale-01")).status_code == 403


def test_a_cashier_cannot_read_another_shops_sales(world, client):
    sid = client.post("/sales", headers=world["cory"], json=sale_body("WST-01", "west-sale-00001")).json()["id"]
    window = {"since": "2020-01-01T00:00:00Z", "until": "2031-01-01T00:00:00Z"}
    assert client.get("/sales", headers=world["cass"], params={**window, "dealership_code": "WST-01"}).status_code == 403
    assert client.get(f"/sales/{sid}", headers=world["cass"]).status_code in (403, 404)
    assert client.get(f"/returns/returnable/{sid}", headers=world["cass"]).status_code in (403, 404)
    assert client.get(f"/sales/{sid}", headers=world["cory"]).status_code == 200
    assert client.get("/sales", headers=world["admin"], params=window).status_code == 200


def test_a_cashier_cannot_refund_or_close_the_day_for_another_shop(world, client):
    sid = client.post("/sales", headers=world["cory"], json=sale_body("WST-01", "west-sale-00002")).json()["id"]
    refund = {"transaction_id": sid, "reason": "x", "client_uuid": "refund-cross-0001", "approver_badge": "B-001",
              "approver_pin": PIN, "lines": [{"barcode": "A1", "quantity": 1, "restock": True}]}
    assert client.post("/returns", headers=world["cass"], json=refund).status_code in (403, 404)
    assert sql("SELECT count(*) FROM sale_returns") == [(0,)]
    assert client.get("/day-close/summary", headers=world["cass"],
                      params={"start": "2020-01-01T00:00:00Z", "end": "2031-01-01T00:00:00Z",
                              "dealership_code": "WST-01"}).status_code in (403, 422)


# --- 2b. one depot manager, one warehouse ------------------------------------------------------

def _shipment(client, h, origin_code, uuid=None):
    eta = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return client.post("/shipments", headers=h, json={
        "origin": origin_code or "Nowhere", "origin_code": origin_code, "dealership_code": "CST-04", "carrier": "Truck", "eta": eta,
        "lines": [{"barcode": "A1", "quantity": 1}]})


def _movement(client, h, code, direction="receive"):
    return client.post("/stock-movements", headers=h, json={
        "direction": direction, "barcode": "A1", "location_kind": "warehouse", "location_code": code, "quantity": 1})


def test_a_depot_manager_books_and_counts_stock_only_at_their_own_warehouse(world, client):
    assert _movement(client, world["mo"], "WH-01").status_code == 201
    assert _movement(client, world["mo"], "WH-02").status_code == 403
    assert _movement(client, world["mia"], "WH-02").status_code == 201
    count = {"barcode": "A1", "place": {"kind": "warehouse", "code": "WH-02"}, "counted": 0}
    assert client.post("/stock/counts", headers=world["mo"], json=count).status_code == 403
    assert client.post("/stock/counts", headers=world["mia"], json=count).status_code == 200
    assert client.post("/stock-movements", headers=world["admin"], json={   # administrators still go anywhere
        "direction": "receive", "barcode": "A1", "location_kind": "warehouse", "location_code": "WH-02",
        "quantity": 1}).status_code == 201


def test_a_depot_manager_ships_only_from_their_own_warehouse(world, client):
    move(client, world["admin"], "receive", 5, kind="warehouse", code="WH-02")
    assert _shipment(client, world["mo"], "WH-02").status_code == 403
    theirs = _shipment(client, world["mia"], "WH-02")
    assert theirs.status_code == 201, theirs.text
    sid = theirs.json()["id"]
    for action, body in (("dispatch", {}), ("eta", {"eta": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}),
                         ("cancel", {})):
        assert client.post(f"/shipments/{sid}/{action}", headers=world["mo"], json=body).status_code == 404, action
    assert client.post(f"/shipments/{sid}/dispatch", headers=world["mia"], json={}).status_code == 200
    assert _shipment(client, world["mo"], None).status_code == 403    # "no origin" is nobody's warehouse


def test_a_depot_manager_sees_and_orders_for_their_own_warehouse_only(world, client):
    def order(h, site):
        return client.post("/purchase-orders", headers=h, json={
            "barcode": "A1", "supplier": "Acme", "quantity": 2, "unit_price": 3, "site": site})

    assert order(world["mo"], "WH-02").status_code == 403
    mine, theirs = order(world["mo"], "WH-01"), order(world["mia"], "WH-02")
    assert mine.status_code == 201 and theirs.status_code == 201, (mine.text, theirs.text)
    listed = client.get("/purchase-orders", headers=world["mo"]).json()
    assert [o["id"] for o in listed] == [mine.json()["id"]]
    assert client.get(f"/purchase-orders/{theirs.json()['id']}", headers=world["mo"]).status_code == 404
    assert client.post(f"/purchase-orders/{theirs.json()['id']}/receive", headers=world["mo"],
                       json={"quantity": 1, "warehouse_code": "WH-02"}).status_code == 403
    assert client.post(f"/purchase-orders/{theirs.json()['id']}/cancel", headers=world["mo"]).status_code == 404
    assert client.post("/purchase-orders/receive-delivery", headers=world["mo"], json={
        "warehouse_code": "WH-02", "barcode": "A1", "quantity": 1}).status_code == 403
    assert len(client.get("/purchase-orders", headers=world["admin"]).json()) == 2


def test_a_depot_manager_reads_and_writes_only_their_own_warehouses_ledger(world, client):
    def entry(h, site, no):
        return client.post("/ledger", headers=h, json={
            "direction": "out", "doc_type": "check", "doc_no": no, "counterparty": f"Party {no}", "site": site,
            "issue_date": "2026-01-01", "due_date": "2026-02-01", "amount": 10})

    assert entry(world["mo"], "WH-02", "X-1").status_code == 403
    assert entry(world["mo"], None, "X-2").status_code == 403                    # company-wide books: administrators
    mine, theirs = entry(world["mo"], "WH-01", "X-3"), entry(world["mia"], "WH-02", "X-4")
    assert mine.status_code == 201 and theirs.status_code == 201, (mine.text, theirs.text)
    assert [e["doc_no"] for e in client.get("/ledger", headers=world["mo"]).json()] == ["X-3"]
    assert client.get(f"/ledger/{theirs.json()['id']}", headers=world["mo"]).status_code == 404
    assert client.get(f"/ledger/{theirs.json()['id']}/audit", headers=world["mo"]).json() == []
    assert {a["entry_id"] for a in client.get("/ledger/audit", headers=world["mo"]).json()} == {mine.json()["id"]}
    names = client.get("/ledger/counterparties", headers=world["mo"]).json()
    assert "Party X-3" in names and "Party X-4" not in names
    assert len(client.get("/ledger", headers=world["admin"]).json()) == 2


def test_a_depot_manager_without_a_warehouse_can_do_nothing_at_a_depot(world, client):
    sql("UPDATE employees SET location_type = 'Dealership', location_name = 'Coast' WHERE badge_id = 'M-1'")
    assert _movement(client, world["mo"], "WH-01").status_code == 403
    assert client.get("/ledger", headers=world["mo"]).status_code == 403
    assert client.get("/purchase-orders", headers=world["mo"]).status_code == 403


# --- 3. privilege escalation -------------------------------------------------------------------

ADMIN_ONLY = [
    ("post", "/accounts", {"badge_id": "C-1", "role": "admin", "pin": "9876543"}),
    ("post", "/accounts/C-1/role", {"role": "admin"}),
    ("post", "/accounts/B-001/pin", {"pin": "9876543"}),
    ("post", "/accounts/B-001/active", {"active": False}),
    ("post", "/accounts/B-001/unlock", None),
    ("delete", "/accounts/B-001", None),
    ("post", "/employees", {"badge_id": "X-9", "name": "Eve", "role": "Management", "location_type": "Warehouse",
                            "location_name": "Main"}),
    ("patch", "/employees/B-001", {"name": "Hacked", "role": "Management", "location_type": "Warehouse",
                                   "location_name": "Main", "title": "x", "is_active": True}),
    ("delete", "/employees/B-001", None),
    ("put", "/settings", {"values": {"store.name": "Hacked"}}),
    ("get", "/settings/private", None),
    ("get", "/audit", None),
    ("get", "/accounts", None),
    ("get", "/accounts/events", None),
    ("post", "/products", {"barcode": "EVIL", "name": "Evil", "price": 0.01, "stock_quantity": 5}),
    ("patch", "/products/A1", {"price": 0.01}),
    ("delete", "/products/A1", None),
    ("post", "/stock-movements", {"direction": "receive", "barcode": "A1", "location_kind": "dealership", "location_code": "CST-04",
                                  "quantity": 99}),
    ("post", "/recovery/code", None),
    ("put", "/recovery/question", {"current_pin": CASHIER_PIN, "question": "Name of my first pet?", "answer": "x"}),
    ("post", "/dealerships", {"code": "EVIL-1", "name": "Evil", "region": "Coastal", "city": "x"}),
    ("delete", "/dealerships/WST-01", None),
    ("delete", "/warehouses/WH-02", None),
    ("get", "/ledger", None),
    ("post", "/ledger", {"direction": "in", "doc_type": "check", "doc_no": "1", "counterparty": "x", "issue_date": "2026-01-01",
                           "due_date": "2026-02-01", "amount": 1}),
    ("put", "/price-ranges/A1", {"min_unit_price": 0.01, "max_unit_price": 1}),
    ("get", "/purchase-orders", None),
    ("get", "/employees", None),
]


@pytest.mark.parametrize("who", ["cass", "mo"])
def test_nobody_below_admin_reaches_administrator_actions(world, client, who):
    wrong = []
    for method, path, body in ADMIN_ONLY:
        r = client.request(method.upper(), path, headers=world[who], json=body)
        allowed_to_depot = who == "mo" and (path.startswith(("/ledger", "/price-ranges", "/purchase-orders"))
                                            or path == "/employees")
        if allowed_to_depot:
            continue
        if r.status_code not in (401, 403):
            wrong.append(f"{who}: {method.upper()} {path} answered {r.status_code}: {r.text[:100]}")
    assert not wrong, "\n".join(wrong)
    # nothing moved
    assert sql("SELECT a.role FROM accounts a JOIN employees e ON e.id = a.employee_id WHERE e.badge_id = 'C-1'") == [
        ("cashier",)]
    assert sql("SELECT price FROM products WHERE barcode = 'A1'") == [(5,)]
    assert sql("SELECT count(*) FROM products") == [(1,)]


def test_a_token_for_one_area_cannot_do_the_work_of_another(world, client):
    # an administrator who signed in to the till is not signed in to Admin
    r = client.post("/products", headers=world["till"], json={"barcode": "T1", "name": "T", "price": 1})
    assert r.status_code in (401, 403)
    r = client.post("/sales", headers=world["admin"], json=sale_body("CST-04", "admin-area-0001"))
    assert r.status_code in (401, 403)


def test_a_cashier_cannot_approve_their_own_refund(world, client):
    sid = client.post("/sales", headers=world["cass"], json=sale_body("CST-04", "own-sale-00002")).json()["id"]
    refund = {"transaction_id": sid, "reason": "x", "client_uuid": "refund-self-0001", "approver_badge": "C-1",
              "approver_pin": CASHIER_PIN, "lines": [{"barcode": "A1", "quantity": 1, "restock": True}]}
    r = client.post("/returns", headers=world["cass"], json=refund)
    assert r.status_code in (401, 403), r.text
    assert sql("SELECT count(*) FROM sale_returns") == [(0,)]


def test_an_offline_sale_cannot_be_dated_in_the_future_or_far_back(world, client):
    now = datetime.now(timezone.utc)
    future = sale_body("CST-04", "future-sale-0001", sold_at=(now + timedelta(days=2)).isoformat())
    ancient = sale_body("CST-04", "ancient-sale-001", sold_at=(now - timedelta(days=400)).isoformat())
    assert client.post("/sales", headers=world["cass"], json=future).status_code == 422
    assert client.post("/sales", headers=world["cass"], json=ancient).status_code == 422
    fresh = sale_body("CST-04", "offline-sale-001", sold_at=(now - timedelta(hours=3)).isoformat())
    assert client.post("/sales", headers=world["cass"], json=fresh).status_code == 200


def test_an_offline_sale_below_todays_price_is_flagged_for_a_person(world, client):
    now = datetime.now(timezone.utc)
    body = sale_body("CST-04", "cheap-offline-01", price=1, sold_at=(now - timedelta(hours=1)).isoformat())
    assert client.post("/sales", headers=world["cass"], json=body).status_code == 200
    assert sql("SELECT severity FROM activity_events WHERE kind = 'price_override'") == [("warning",)]


# --- 4. hostile input ----------------------------------------------------------------------------

PAYLOADS = ["' OR '1'='1", "'; DROP TABLE products; --", "\" OR 1=1 --", "A1' UNION SELECT pin_hash FROM accounts --",
            "a\x00b", "\x00", "%00", "../../etc/passwd", "{{7*7}}", "${jndi:ldap://x}", "<script>alert(1)</script>",
            "𝕬" * 40, "é" * 300, "A" * 5000, "-1", "9" * 40, "1e999", "NaN", "Infinity", " ", ""]


def _fill(schema, spec, value):
    """A request body for `schema` with `value` wherever a string goes and plain numbers elsewhere."""
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _fill(options[0], spec, value) if options else None
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {k: _fill(v, spec, value) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [_fill(schema["items"], spec, value)]
    if kind == "string":
        if "enum" in schema:
            return schema["enum"][0]
        return value
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.5
    if kind == "boolean":
        return True
    return value


def test_hostile_values_in_any_field_of_any_endpoint_never_cause_a_server_error(world, client):
    spec = client.app.openapi()
    before = {t: sql(f"SELECT count(*) FROM {t}")[0][0] for t in ("accounts", "employees")}
    hashes = sql("SELECT id, pin_hash FROM accounts ORDER BY id")
    failures = []
    for method, path, op in _operations(client):
        if (method, path) in PUBLIC and path in ("/health", "/health/db"):
            continue
        schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        for payload in PAYLOADS:
            url = re.sub(r"\{[^}]*\}", lambda m: quote(payload, safe="") if payload.strip() else "1", path)
            body = _fill(schema, spec, payload) if schema else None
            query = {p["name"]: payload for p in op.get("parameters", []) if p["in"] == "query"}
            try:
                r = client.request(method.upper(), url, headers=world["admin"], json=body, params=query)
            except Exception as exc:  # an unhandled error inside the app surfaces here
                failures.append(f"{method.upper()} {path} {payload[:20]!r}: raised {type(exc).__name__}: {exc}")
                continue
            if r.status_code >= 500:
                failures.append(f"{method.upper()} {path} {payload[:20]!r}: {r.status_code}")
    assert not failures, "\n".join(failures[:40])
    assert before == {t: sql(f"SELECT count(*) FROM {t}")[0][0] for t in before}
    assert hashes == sql("SELECT id, pin_hash FROM accounts ORDER BY id")


def test_a_login_with_injection_in_the_badge_is_just_a_wrong_badge(client):
    for badge in ("' OR 1=1 --", "B-001' --", "B-001\x00", "b-001 OR 1=1"):
        r = client.post("/auth/login", json={"badge_id": badge, "pin": PIN, "area": "admin"})
        assert r.status_code in (401, 422), (badge, r.status_code)   # never a server error, never a sign-in


def test_a_wrong_pin_and_an_unknown_badge_look_the_same(client):
    a = client.post("/auth/login", json={"badge_id": "B-001", "pin": "000000", "area": "admin"})
    b = client.post("/auth/login", json={"badge_id": "NOPE-1", "pin": "000000", "area": "admin"})
    assert (a.status_code, a.json()) == (b.status_code, b.json())


def test_the_api_has_no_documentation_pages_and_leaks_no_server_details(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    r = client.get("/health")
    assert "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "").lower()
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "no-store" in r.headers.get("cache-control", "")
    assert r.headers.get("x-frame-options") == "DENY"


def test_a_huge_body_is_refused_before_it_is_read(client):
    big = "x" * (3 * 1024 * 1024)
    r = client.post("/auth/login", content=('{"badge_id": "%s", "pin": "1", "area": "admin"}' % big).encode(),
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.post("/auth/login", content=b"{" * 100_000, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 413, 422)


def test_many_wrong_guesses_from_one_address_are_slowed_down(client):
    codes = [client.post("/auth/login", json={"badge_id": f"GUESS-{i}", "pin": "000000", "area": "admin"}).status_code
             for i in range(80)]
    assert 429 in codes, "80 guesses in a row were never slowed down"
    assert codes.index(429) <= 40


def test_slowing_one_address_down_does_not_lock_the_real_user_out_elsewhere(client):
    for i in range(80):
        client.post("/auth/login", json={"badge_id": f"GUESS-{i}", "pin": "000000", "area": "admin"})
    # the limiter is per address: another address still signs in
    r = client.post("/auth/login", json={"badge_id": "B-001", "pin": PIN, "area": "admin"},
                    headers={"X-Forwarded-For": "203.0.113.9"})
    assert r.status_code in (200, 429)


# --- 5. races ------------------------------------------------------------------------------------

def _many(settings, n, call):
    """Run `call(client, i)` n times at once, each on its own connection; return the results."""
    out = [None] * n
    gate = threading.Barrier(n)

    def run(i):
        c = TestClient(create_app(settings))
        gate.wait()
        out[i] = call(c, i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out


def test_the_last_unit_cannot_be_sold_twice_by_simultaneous_tills(world, client):
    sql("UPDATE stock_levels SET quantity = 1 WHERE location_code = 'CST-04'")
    sql("UPDATE products SET stock_quantity = 6")
    settings = Settings(database_url=URL, jwt_secret=SECRET)
    results = _many(settings, 8, lambda c, i: c.post(
        "/sales", headers=world["cass"], json=sale_body("CST-04", f"race-sale-{i:04d}")).status_code)
    assert sorted(results).count(200) == 1, results
    assert sql("SELECT quantity FROM stock_levels WHERE location_code = 'CST-04'") == [(0,)]
    assert sql("SELECT count(*) FROM transactions") == [(1,)]


def test_the_same_sale_sent_many_times_at_once_is_stored_once(world, client):
    settings = Settings(database_url=URL, jwt_secret=SECRET)
    results = _many(settings, 8, lambda c, i: c.post(
        "/sales", headers=world["cass"], json=sale_body("CST-04", "same-key-000001")).status_code)
    assert set(results) == {200}
    assert sql("SELECT count(*) FROM transactions") == [(1,)]
    assert sql("SELECT quantity FROM stock_levels WHERE location_code = 'CST-04'") == [(4,)]


def test_the_same_units_cannot_be_refunded_twice_at_once(world, client):
    sid = client.post("/sales", headers=world["cass"], json=sale_body("CST-04", "refund-race-sale")).json()["id"]
    settings = Settings(database_url=URL, jwt_secret=SECRET)

    def refund(c, i):
        return c.post("/returns", headers=world["cass"], json={
            "transaction_id": sid, "reason": "x", "client_uuid": f"refund-race-{i:04d}", "approver_badge": "B-001",
            "approver_pin": PIN, "lines": [{"barcode": "A1", "quantity": 1, "restock": True}]}).status_code

    results = _many(settings, 6, refund)
    assert sorted(results).count(200) == 1, results
    assert sql("SELECT count(*) FROM sale_returns") == [(1,)]
    assert sql("SELECT quantity FROM stock_levels WHERE location_code = 'CST-04'") == [(5,)]


# --- 6. every field has a ceiling ---------------------------------------------------------------

def _unbounded(schema, spec, where, seen=()):
    """Strings without maxLength, lists without maxItems and numbers without a ceiling in a request model."""
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if name in seen:
            return []
        return _unbounded(spec["components"]["schemas"][name], spec, where, seen + (name,))
    problems = []
    for key in ("anyOf", "oneOf", "allOf"):
        options = schema.get(key, [])
        decimal = any(o.get("type") == "number" for o in options)   # a Decimal also travels as text; its range is checked
        for option in options:
            if decimal and option.get("type") == "string":
                continue
            problems += _unbounded(option, spec, where, seen)
    kind = schema.get("type")
    if kind == "string" and "maxLength" not in schema and "enum" not in schema and "const" not in schema \
            and schema.get("format") not in ("date", "date-time") and "pattern" not in schema:
        problems.append(f"{where}: text without maxLength")
    if kind == "array":
        if "maxItems" not in schema:
            problems.append(f"{where}: list without maxItems")
        problems += _unbounded(schema.get("items", {}), spec, where + "[]", seen)
    if kind in ("integer", "number") and not ("maximum" in schema or "exclusiveMaximum" in schema):
        problems.append(f"{where}: number without a ceiling")
    for name, prop in schema.get("properties", {}).items():
        problems += _unbounded(prop, spec, f"{where}.{name}", seen)
    if isinstance(schema.get("additionalProperties"), dict) and "maxProperties" not in schema:
        problems.append(f"{where}: free-form map")
    return problems


def test_every_request_field_has_a_ceiling(client):
    spec = client.app.openapi()
    problems = []
    for method, path, op in _operations(client):
        schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if schema:
            problems += _unbounded(schema, spec, f"{method.upper()} {path}")
    assert not problems, "\n".join(sorted(set(problems)))
