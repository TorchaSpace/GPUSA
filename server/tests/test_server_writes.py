"""Product create/update and stock movements, against a real PostgreSQL."""

import os
import pathlib

import pytest

pytest.importorskip("alembic")
pytest.importorskip("jwt")

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gpusa_server import create_admin  # noqa: E402
from gpusa_server.config import Settings  # noqa: E402
from gpusa_server.main import create_app  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test the writes")

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
PIN = "482915"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", URL)
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(cfg, "head")
    yield
    mp.undo()


def sql(query, params=()):
    with psycopg.connect(URL, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture()
def client():
    settings = Settings(database_url=URL, jwt_secret="x" * 40)
    sql("TRUNCATE accounts, auth_events, employees, products, dealerships, warehouses, stock_movements, "
        "admin_audit RESTART IDENTITY CASCADE")
    sql("INSERT INTO dealerships (code, name, region, city) VALUES ('CST-04', 'Coast', 'Coastal', 'Izmir')")
    sql("INSERT INTO warehouses (code, name, capacity_units) VALUES ('WH-01', 'Main', 10), ('WH-02', 'Spare', NULL)")
    sql("INSERT INTO products (barcode, name, price) VALUES ('A1', 'Anvil', 5)")
    create_admin.create_admin(settings, "B-001", "Ali", PIN)
    return TestClient(create_app(settings))


def login(client, area="admin"):
    r = client.post("/auth/login", json={"badge_id": "B-001", "pin": PIN, "area": area})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def total(barcode):
    return sql("SELECT stock_quantity FROM products WHERE barcode = %s", (barcode,))[0][0]


def level(kind, code, barcode):
    rows = sql("SELECT quantity FROM stock_levels WHERE location_kind = %s AND location_code = %s "
               "AND product_barcode = %s", (kind, code, barcode))
    return rows[0][0] if rows else 0


# --- products ---------------------------------------------------------------

def test_writes_need_a_token(client):
    assert client.post("/products", json={}).status_code == 401
    assert client.patch("/products/A1", json={}).status_code == 401
    assert client.post("/stock-movements", json={}).status_code == 401


def test_create_product_with_starting_stock(client):
    h = login(client)
    r = client.post("/products", headers=h, json={"barcode": " nb-7 ", "name": " Nail box ", "price": 19.999,
                                                  "stock_quantity": 4, "critical_stock_level": 2})
    assert r.status_code == 201, r.text
    assert r.json() == {"barcode": "NB-7", "name": "Nail box", "price": 20.0, "stock_quantity": 4,
                        "critical_stock_level": 2, "is_active": True, "cost_price": 0.0}
    assert level("unassigned", "", "NB-7") == 4
    assert sql("SELECT movement_type, quantity, handled_by FROM stock_movements") == [("receive", 4, "Ali · B-001")]
    assert sql("SELECT action, target FROM admin_audit") == [("created", "NB-7")]


def test_get_one_product_and_cost_only_for_managers(client):
    h = login(client)
    client.patch("/products/A1", headers=h, json={"cost_price": 3.333})
    one = client.get("/products/a1", headers=h).json()
    assert (one["barcode"], one["cost_price"]) == ("A1", 3.33)
    assert client.get("/products/NOPE", headers=h).status_code == 404
    sql("UPDATE accounts SET role = 'cashier'")
    pos = login(client, "pos")
    assert client.get("/products/A1", headers=pos).json()["cost_price"] is None
    assert client.get("/products", headers=pos).json()[0]["cost_price"] is None


def test_create_duplicate_barcode_any_case(client):
    h = login(client)
    r = client.post("/products", headers=h, json={"barcode": "a1", "name": "Other", "price": 1})
    assert r.status_code == 409


@pytest.mark.parametrize("bad", [{"price": -1}, {"price": 2e9}, {"name": "   "}, {"barcode": "  "},
                                 {"stock_quantity": -1}, {"critical_stock_level": 1.5}])
def test_create_validation(client, bad):
    body = {"barcode": "Z9", "name": "Zip", "price": 1, **bad}
    assert client.post("/products", headers=login(client), json=body).status_code == 422


def test_update_product_and_audit(client):
    h = login(client)
    r = client.patch("/products/a1", headers=h, json={"price": 7.5, "is_active": False, "name": "Anvil XL"})
    assert r.status_code == 200, r.text
    assert (r.json()["price"], r.json()["is_active"], r.json()["name"]) == (7.5, False, "Anvil XL")
    detail = sql("SELECT detail FROM admin_audit WHERE action = 'updated'")[0][0]
    assert "price: 5.00 -> 7.50" in detail and "is_active: True -> False" in detail
    # Sending the same values again changes nothing and logs nothing.
    client.patch("/products/A1", headers=h, json={"price": 7.5})
    assert len(sql("SELECT 1 FROM admin_audit")) == 1
    assert client.patch("/products/NOPE", headers=h, json={"price": 1}).status_code == 404


def test_update_cannot_touch_stock(client):
    r = client.patch("/products/A1", headers=login(client), json={"stock_quantity": 99})
    assert r.status_code == 200 and total("A1") == 0


def test_only_admins_in_admin_area_write_products(client):
    pos = login(client, "pos")  # an admin signed in to a till
    assert client.post("/products", headers=pos, json={"barcode": "Q", "name": "Q", "price": 1}).status_code == 403
    sql("UPDATE accounts SET role = 'depot_manager'")
    depot = login(client, "depot_console")
    assert client.patch("/products/A1", headers=depot, json={"price": 1}).status_code == 403


def test_demoted_admin_with_old_token_is_refused(client):
    h = login(client)
    sql("UPDATE accounts SET role = 'cashier'")
    r = client.patch("/products/A1", headers=h, json={"price": 9})
    assert r.status_code == 401
    assert sql("SELECT price FROM products")[0][0] == 5


def test_switched_off_account_with_old_token_is_refused(client):
    h = login(client)
    sql("UPDATE accounts SET is_active = FALSE")
    assert client.post("/stock-movements", headers=h, json={
        "direction": "receive", "barcode": "A1", "location_kind": "warehouse", "location_code": "WH-01",
        "quantity": 1}).status_code == 401


# --- stock movements --------------------------------------------------------

def move(client, h, direction, qty, kind="warehouse", code="WH-01", barcode="A1", **extra):
    return client.post("/stock-movements", headers=h, json={
        "direction": direction, "barcode": barcode, "location_kind": kind, "location_code": code,
        "quantity": qty, **extra})


def test_receive_and_dispatch_keep_level_and_total_together(client):
    h = login(client)
    r = move(client, h, "receive", 6, code="wh-01", barcode="a1", note=" from supplier ", reference="PO-1")
    assert r.status_code == 201, r.text
    assert (r.json()["level_after"], r.json()["note"], r.json()["handled_by"]) == (6, "from supplier", "Ali · B-001")
    assert (level("warehouse", "WH-01", "A1"), total("A1")) == (6, 6)
    assert move(client, h, "dispatch", 2).json()["level_after"] == 4
    assert (level("warehouse", "WH-01", "A1"), total("A1")) == (4, 4)
    listed = client.get("/stock-movements?product_barcode=a1", headers=h).json()
    assert [m["direction"] for m in listed] == ["dispatch", "receive"]


def test_dispatch_never_goes_below_zero(client):
    h = login(client)
    move(client, h, "receive", 3)
    assert move(client, h, "dispatch", 4).status_code == 409
    assert (level("warehouse", "WH-01", "A1"), total("A1")) == (3, 3)


def test_warehouse_capacity_and_switched_off(client):
    h = login(client)
    assert move(client, h, "receive", 11).status_code == 409  # capacity 10
    assert move(client, h, "receive", 50, code="WH-02").status_code == 201  # no limit
    sql("UPDATE warehouses SET is_active = FALSE WHERE code = 'WH-02'")
    assert move(client, h, "receive", 1, code="WH-02").status_code == 409
    assert move(client, h, "dispatch", 1, code="WH-02").status_code == 201  # stock may still leave


def test_unknown_place_or_product_and_inactive_product(client):
    h = login(client)
    assert move(client, h, "receive", 1, code="WH-99").status_code == 404
    assert move(client, h, "receive", 1, barcode="NOPE").status_code == 404
    assert move(client, h, "receive", 1, kind="dealership", code="").status_code == 422
    assert move(client, h, "receive", 0).status_code == 422
    sql("UPDATE products SET is_active = FALSE")
    assert move(client, h, "receive", 1).status_code == 409


def test_depot_manager_books_at_warehouses_only(client):
    sql("UPDATE accounts SET role = 'depot_manager'")
    sql("UPDATE employees SET location_type = 'Warehouse', location_name = 'WH-01'")
    h = login(client, "depot_console")
    assert move(client, h, "receive", 2).status_code == 201
    assert move(client, h, "receive", 2, kind="dealership", code="CST-04").status_code == 403
    assert client.get("/stock-movements", headers=h).status_code == 200


def test_cashier_cannot_book_or_list(client):
    sql("UPDATE accounts SET role = 'cashier'")
    h = login(client, "pos")
    assert move(client, h, "receive", 1, kind="dealership", code="CST-04").status_code == 403
    assert client.get("/stock-movements", headers=h).status_code == 403
