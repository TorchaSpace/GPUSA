"""End to end: the desktop repositories in server mode against a real API
process (uvicorn on a free port) and a real PostgreSQL."""

import os
import pathlib
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("alembic")
pytest.importorskip("uvicorn")

import psycopg  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from gpusa_server import create_admin  # noqa: E402
from gpusa_server.config import Settings  # noqa: E402
from gpusa_server.main import create_app  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test server mode")

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO = SERVER_DIR.parent
PIN = "482915"


@pytest.fixture(scope="module")
def api_url():
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", URL)
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(cfg, "head")
    mp.undo()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(database_url=URL, jwt_secret="x" * 40)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"127.0.0.1:{port}", settings
    server.should_exit = True
    thread.join(5)


@pytest.fixture()
def desktop(api_url, monkeypatch):
    """The desktop packages (repo root) with server mode on, signed in as an admin."""
    url, settings = api_url
    monkeypatch.syspath_prepend(str(REPO))
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE accounts, auth_events, employees, products, dealerships, warehouses, "
                     "stock_movements, admin_audit RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO warehouses (code, name) VALUES ('WH-01', 'Main')")
    create_admin.create_admin(settings, "B-001", "Ali", PIN)
    from database import remote
    from shared.server_client import ServerClient

    client = ServerClient(url)
    client.login("B-001", PIN)
    remote.use_server(client)
    yield
    remote.use_server(None)


def test_products_and_stock_through_the_desktop_repositories(desktop):
    from database import product_repository, stock_repository
    from database.exceptions import DuplicateBarcodeError, InsufficientStockError
    from shared.models import Product, StockLocation

    product_repository.create(Product("nb-7", "Nail box", 19.999, 0, 2))
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(Product("NB-7", "Again", 1, 0))
    product_repository.update(Product("NB-7", "Nail box XL", 21, 0, 3))
    assert product_repository.set_cost("NB-7", 8.5) == 8.5
    wh = StockLocation("warehouse", "WH-01")
    assert stock_repository.receive(wh, "nb-7", 10, note="supplier") == 10
    assert stock_repository.dispatch(wh, "NB-7", 4) == 6
    with pytest.raises(InsufficientStockError) as info:
        stock_repository.dispatch(wh, "NB-7", 7)
    assert info.value.available == 6
    [p] = product_repository.list_all()
    assert (p.barcode, p.name, p.price, p.stock_quantity, p.critical_stock_level, p.cost_price) == \
        ("NB-7", "Nail box XL", 21.0, 6, 3, 8.5)
    [level] = stock_repository.levels_for_product("NB-7")
    assert (level.location, level.quantity) == (wh, 6)
    product_repository.set_active("NB-7", False)
    assert product_repository.list_active() == []
    with psycopg.connect(URL) as conn:
        who = conn.execute("SELECT DISTINCT handled_by FROM stock_movements").fetchall()
    assert who == [("Ali · B-001",)]


def test_places_transfers_counts_and_reads_through_the_desktop(desktop):
    from database import dealership_repository, product_repository, stock_repository, warehouse_repository
    from database.exceptions import InsufficientStockError
    from shared.models import Product, StockLocation

    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("INSERT INTO dealerships (code, name, region, city) VALUES ('CST-04', 'Coast', 'Coastal', 'Izmir')")
    wh, shop = StockLocation("warehouse", "WH-01"), StockLocation("dealership", "CST-04")
    assert [w.code for w in warehouse_repository.list_all()] == ["WH-01"]
    assert dealership_repository.get_by_code("cst-04").name == "Coast"
    product_repository.create(Product("A1", "Anvil", 5, 6, 2))  # 6 unplaced
    assert stock_repository.place_all_unassigned(wh) == 6
    stock_repository.transfer(wh, shop, "A1", 4)
    with pytest.raises(InsufficientStockError):
        stock_repository.distribute(wh, "A1", [(shop, 3)])
    assert stock_repository.set_count(shop, "A1", 3) == -1
    stock_repository.set_reorder_level(shop, "A1", 3)
    [here] = stock_repository.products_at(shop)
    assert (here.stock_quantity, here.critical_stock_level) == (3, 3)
    assert stock_repository.critical_at(shop) == [here]
    assert stock_repository.units_by_location() == {wh: 2, shop: 3}
    assert product_repository.get_by_barcode("A1").stock_quantity == 5  # the count lost one
    moves = stock_repository.list_movements(limit=None, location=shop)
    assert [(m["reason"], m["movement_type"], m["quantity"]) for m in moves] == [("count", "dispatch", 1),
                                                                               ("transfer", "receive", 4)]
    assert moves[0]["created_at"].endswith("Z")
    since = moves[-1]["created_at"]
    assert stock_repository.movement_totals_since(since)[shop] == (4, 1)
    assert set(stock_repository.last_movement_at(shop)) == {"A1"}


def test_desktop_sign_in_goes_to_the_server(api_url, monkeypatch, tmp_path):
    url, settings = api_url
    monkeypatch.syspath_prepend(str(REPO))
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE accounts, auth_events, employees, products RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO products (barcode, name, price) VALUES ('A1', 'Anvil', 5)")
    create_admin.create_admin(settings, "B-001", "Ali", PIN)
    import database.connection as connection
    from database import account_repository, product_repository, remote
    from database.exceptions import SignInFailedError
    from shared import paths

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "local.db")
    monkeypatch.setattr(connection, "_initialized", False)
    monkeypatch.setenv(paths.SERVER_URL_ENV, url)
    try:
        with pytest.raises(SignInFailedError):
            account_repository.authenticate("B-001", "000000", "admin", "test")
        session = account_repository.authenticate("b-001", PIN, "admin", "test")
        assert (session.badge_id, session.role) == ("B-001", "admin")
        assert [p.barcode for p in product_repository.list_all()] == ["A1"]
        with psycopg.connect(URL) as conn:
            events = [r[0] for r in conn.execute("SELECT event FROM auth_events ORDER BY id")]
        assert events == ["failed", "sign_in"]
        account_repository.sign_out(session)
        assert not remote.active()
    finally:
        remote.use_server(None)
