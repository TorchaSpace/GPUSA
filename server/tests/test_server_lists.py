"""The read-only list endpoints, against a real PostgreSQL."""

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
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test the lists")

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


@pytest.fixture()
def client():
    settings = Settings(database_url=URL, jwt_secret="x" * 40)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE accounts, auth_events, employees, products, dealerships, warehouses "
                     "RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO dealerships (code, name, region, city) VALUES ('CST-04', 'Coast', 'Coastal', 'Izmir')")
        conn.execute("INSERT INTO warehouses (code, name) VALUES ('WH-01', 'Main')")
        conn.execute("INSERT INTO products (barcode, name, price) VALUES ('A1', 'Anvil', 1), ('B2', 'Bolt', 2)")
        conn.execute("INSERT INTO stock_levels VALUES ('warehouse', 'WH-01', 'A1', 7, now()), "
                     "('dealership', 'CST-04', 'A1', 3, now()), ('dealership', 'CST-04', 'B2', 5, now())")
    create_admin.create_admin(settings, "B-001", "Ali", PIN)
    return TestClient(create_app(settings))


def headers(client, area="admin"):
    r = client.post("/auth/login", json={"badge_id": "B-001", "pin": PIN, "area": area})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_lists_need_a_token(client):
    for path in ("/dealerships", "/warehouses", "/employees", "/stock-levels"):
        assert client.get(path).status_code == 401


def test_dealerships_and_warehouses(client):
    h = headers(client)
    assert client.get("/dealerships", headers=h).json()[0]["code"] == "CST-04"
    assert client.get("/warehouses", headers=h).json()[0]["code"] == "WH-01"


def test_stock_levels_and_filters(client):
    h = headers(client)
    assert len(client.get("/stock-levels", headers=h).json()) == 3
    rows = client.get("/stock-levels?location_kind=dealership&location_code=CST-04", headers=h).json()
    assert sorted(r["product_barcode"] for r in rows) == ["A1", "B2"]
    one = client.get("/stock-levels?product_barcode=a1&location_kind=warehouse", headers=h).json()
    assert [(r["location_code"], r["quantity"]) for r in one] == [("WH-01", 7)]


def test_employees_only_for_admins_and_depot_managers(client):
    h = headers(client)
    assert client.get("/employees", headers=h).json()[0]["badge_id"] == "B-001"
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET role = 'cashier'")
    assert client.get("/employees", headers=headers(client, "pos")).status_code == 403
    assert client.get("/dealerships", headers=headers(client, "pos")).status_code == 200
