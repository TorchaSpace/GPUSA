"""Sign-in and the product list, against a real PostgreSQL (schema from Alembic)."""

import os
import pathlib

import pytest

pytest.importorskip("alembic")
pytest.importorskip("jwt")

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from gpusa_server import create_admin, security  # noqa: E402
from gpusa_server.config import Settings  # noqa: E402
from gpusa_server.main import create_app  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test sign-in")

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
SECRET = "x" * 40
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
def settings():
    s = Settings(database_url=URL, jwt_secret=SECRET)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE accounts, auth_events, employees, products RESTART IDENTITY CASCADE")
    create_admin.create_admin(s, "b-001", "Ali Emre", PIN)
    return s


@pytest.fixture()
def client(settings):
    return TestClient(create_app(settings))


def login(client, badge="B-001", pin=PIN, area="admin"):
    return client.post("/auth/login", json={"badge_id": badge, "pin": pin, "area": area, "terminal": "t1"})


def events():
    with psycopg.connect(URL) as conn:
        return [r[0] for r in conn.execute("SELECT event FROM auth_events ORDER BY id").fetchall()]


def test_login_works_in_any_letter_case_and_logs_it(client):
    r = login(client, badge=" b-001 ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["badge_id"], body["name"], body["role"]) == ("B-001", "Ali Emre", "admin")
    assert events() == ["root_sign_in"]  # the root user's sign-ins are logged under their own event
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.json()["badge_id"] == "B-001"


def test_wrong_pin_and_unknown_badge_look_the_same(client):
    a = login(client, pin="111222")
    b = login(client, badge="NOPE")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_five_wrong_pins_lock_the_account(client):
    for _ in range(4):
        assert login(client, pin="111222").status_code == 401
    assert login(client, pin="111222").status_code == 423
    # Even the right PIN is refused while locked.
    assert login(client).status_code == 423
    assert "locked" in events()


def test_correct_pin_clears_the_wrong_count(client):
    for _ in range(3):
        login(client, pin="111222")
    assert login(client).status_code == 200
    for _ in range(4):
        assert login(client, pin="111222").status_code == 401  # count started again from 0


def test_role_must_fit_the_area_and_disabled_accounts_are_refused(client, settings):
    # An administrator may open every area.
    assert login(client, area="pos").status_code == 200
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET role = 'cashier'")
    assert login(client, area="admin").status_code == 200  # IAM users open Admin too
    assert login(client, area="depot_console").status_code == 403
    assert login(client, area="pos").status_code == 200
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET is_active = FALSE")
    assert login(client, area="pos").status_code == 403


def test_old_pin_hash_from_the_desktop_works_and_is_upgraded(client):
    old = security.hash_pin(PIN, iterations=1000)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET pin_hash = %s", (old,))
    assert login(client).status_code == 200
    with psycopg.connect(URL) as conn:
        new = conn.execute("SELECT pin_hash FROM accounts").fetchone()[0]
    assert new != old and not security.needs_rehash(new)


def test_login_is_off_without_a_jwt_secret(settings):
    c = TestClient(create_app(Settings(database_url=URL)))
    assert login(c).status_code == 503


def test_products_need_a_token_and_hide_inactive_ones(client):
    assert client.get("/products").status_code == 401
    assert client.get("/products", headers={"Authorization": "Bearer junk"}).status_code == 401
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("INSERT INTO products (barcode, name, price, stock_quantity) VALUES ('A1', 'Anvil', 9.5, 3)")
        conn.execute("INSERT INTO products (barcode, name, price, is_active) VALUES ('B2', 'Old', 1, FALSE)")
    token = login(client).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    rows = client.get("/products", headers=h).json()
    assert [r["barcode"] for r in rows] == ["A1"]
    assert rows[0]["price"] == 9.5 and rows[0]["stock_quantity"] == 3
    assert len(client.get("/products?include_inactive=true", headers=h).json()) == 2


def test_create_admin_rejects_weak_pins(settings):
    for bad in ("1234", "123456", "111111", "12ab56"):
        with pytest.raises(ValueError):
            create_admin.create_admin(settings, "B-9", "X", bad)
