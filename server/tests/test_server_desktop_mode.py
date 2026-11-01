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
