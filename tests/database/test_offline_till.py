"""Server mode at the till, offline-first: sales queue locally when the
server cannot be reached and are sent later, once each."""

from __future__ import annotations

import json

import pytest

import database.connection as connection
from database import account_repository as accounts, remote, remote_sales, stock_repository, transaction_repository
from database.exceptions import InsufficientStockError, PriceChangedError, SignInFailedError
from shared import auth, paths
from shared.models import LineItem, StockLocation, Transaction
from shared.server_client import ServerError
from tests.server_support import FakeServer

SHOP = StockLocation("dealership", "CST-04")
SHELF = [{"barcode": "A1", "name": "Anvil", "price": 5.0, "quantity": 4, "critical_stock_level": 1,
          "is_active": True, "stocked_here": True}]
SHELF_PATH = "/stock/products-at?kind=dealership&code=CST-04"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


class Till:
    """A fake signed-in client: `down` makes every call fail like a dead network."""

    base_url = "http://server.test"
    token = "tok"

    def __init__(self):
        self.down = False
        self.sales: dict[str, dict] = {}
        self.refuse: str | None = None

    def _maybe_down(self):
        if self.down:
            raise ServerError("Cannot reach the server (timed out).")

    def get(self, path):
        self._maybe_down()
        if path == SHELF_PATH:
            return [dict(r) for r in SHELF]
        raise ServerError("Not found", 404)

    def post(self, path, body):
        self._maybe_down()
        assert path == "/sales"
        if self.refuse:
            raise ServerError(self.refuse, 409)
        first = body["client_uuid"] not in self.sales
        self.sales.setdefault(body["client_uuid"], body)
        return {"id": list(self.sales).index(body["client_uuid"]) + 1, "client_uuid": body["client_uuid"],
                "created_at": body.get("sold_at") or "2026-11-01T10:00:00+00:00", "total": 10.0,
                "dealership_code": body["dealership_code"], "cashier": "Ali · B-001",
                "payment_method": body["payment_method"], "duplicate": not first,
                "lines": [{**ln, "unit_cost": 0, "cost_known": False} for ln in body["lines"]]}


def sale(qty=2, price=5.0):
    return Transaction(items=[LineItem("A1", "Anvil", price, qty)], payment_method="cash")


@pytest.fixture
def till():
    t = Till()
    remote.use_server(t)
    return t


def test_online_sale_goes_straight_to_the_server(till):
    done = transaction_repository.finalize_transaction(sale(), SHOP)
    assert done.id == 1 and remote_sales.pending_count() == 0
    [sent] = till.sales.values()
    assert "sold_at" not in sent and sent["lines"][0]["quantity"] == 2


def test_offline_sale_queues_then_goes_once(till):
    stock_repository.products_at(SHOP)  # seen once online: now cached
    till.down = True
    done = transaction_repository.finalize_transaction(sale(), SHOP)
    assert done.id is None and done.client_uuid and remote_sales.pending_count() == 1
    [p] = stock_repository.products_at(SHOP)  # from the cache, less what was sold
    assert p.stock_quantity == 2
    assert remote_sales.send_pending() == {"sent": 0, "rejected": 0, "pending": 1}  # still offline
    assert [t.id for t in transaction_repository.list_between(*_today())] == [None]
    till.down = False
    assert remote_sales.send_pending() == {"sent": 1, "rejected": 0, "pending": 0}
    [sent] = till.sales.values()
    assert sent["client_uuid"] == done.client_uuid and "sold_at" in sent
    assert remote_sales.send_pending()["sent"] == 0  # nothing sent twice


def test_a_refused_queued_sale_is_kept_aside(till):
    till.down = True
    transaction_repository.finalize_transaction(sale(), SHOP)
    till.down, till.refuse = False, "Only 0 units of A1 at CST-04; the sale needs 2."
    assert remote_sales.send_pending() == {"sent": 0, "rejected": 1, "pending": 0}
    [kept] = remote_sales.rejected_sales()
    assert "Only 0 units" in kept["error"] and kept["payload"]["lines"][0]["barcode"] == "A1"


def test_live_refusals_are_the_cart_errors_and_nothing_queues(till):
    till.refuse = "Prices changed: A1: 4.99 -> 5.00"
    with pytest.raises(PriceChangedError) as info:
        transaction_repository.finalize_transaction(sale(price=4.99), SHOP)
    assert info.value.changes == [("A1", 4.99, 5.0)]
    till.refuse = "Only 1 units of A1 at CST-04; the sale needs 2."
    with pytest.raises(InsufficientStockError):
        transaction_repository.finalize_transaction(sale(), SHOP)
    assert remote_sales.pending_count() == 0


def test_the_same_sale_sent_twice_is_one_sale(till):
    t = sale()
    first = transaction_repository.finalize_transaction(t, SHOP)
    again = transaction_repository.finalize_transaction(
        Transaction(items=t.items, payment_method="cash", client_uuid=first.client_uuid), SHOP)
    assert again.id == first.id and len(till.sales) == 1


def _today():
    from datetime import datetime, timedelta
    now = datetime.now()
    return now - timedelta(days=1), now + timedelta(days=1)


# --- signing in at an offline till ---------------------------------------------

def test_offline_sign_in_needs_an_earlier_online_one():
    server = FakeServer()
    paths.set_server_url(server.url)
    accounts.authenticate("B-001", "482915", auth.AREA_POS, "Till 1")  # online once: remembered
    remote.use_server(None)
    server.close()  # the network goes
    session = accounts.authenticate("b-001", "482915", auth.AREA_POS, "Till 1")
    assert (session.badge_id, session.name) == ("B-001", "Ali")
    assert remote.active() and not remote.online()
    with pytest.raises(SignInFailedError):
        accounts.authenticate("B-001", "000000", auth.AREA_POS, "Till 1")
    with pytest.raises(remote.ServerUnavailableError):
        accounts.authenticate("B-404", "482915", auth.AREA_POS, "Till 1")  # never signed in here


def test_pin_hash_only_is_kept():
    server = FakeServer()
    try:
        paths.set_server_url(server.url)
        accounts.authenticate("B-001", "482915", auth.AREA_POS, "Till 1")
        with connection.connection_scope() as conn:
            row = conn.execute("SELECT * FROM server_sign_ins").fetchone()
        assert "482915" not in json.dumps(dict(row)) and row["pin_hash"].startswith("pbkdf2_sha256$")
    finally:
        server.close()
