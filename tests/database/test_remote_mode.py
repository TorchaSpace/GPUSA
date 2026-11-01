"""Server mode (database/remote.py): repository calls go to the API, answers
come back as the usual models and exceptions, and local mode is untouched."""

from __future__ import annotations

import pytest

from database import product_repository, remote, stock_repository
from database.exceptions import (
    DATABASE_ERRORS,
    DuplicateBarcodeError,
    InsufficientStockError,
    ProductInactiveError,
    ProductNotFoundError,
    SessionInvalidError,
    UnknownLocationError,
)
from shared.i18n import UserError
from shared.models import Product, StockLocation
from shared.server_client import ServerError

ROW = {"barcode": "A1", "name": "Anvil", "price": 9.5, "stock_quantity": 3, "critical_stock_level": 1,
       "is_active": True, "cost_price": 4.0}
WH = StockLocation("warehouse", "WH-01")


class FakeClient:
    """Records calls; `answers` maps (method, path) to a value or a ServerError."""

    base_url = "http://server.test"
    token = "tok"

    def __init__(self, answers: dict):
        self.answers = answers
        self.calls: list[tuple] = []

    def _answer(self, method, path, body=None):
        self.calls.append((method, path, body))
        value = self.answers[(method, path)]
        if isinstance(value, ServerError):
            raise value
        return value

    def get(self, path):
        return self._answer("GET", path)

    def post(self, path, body):
        return self._answer("POST", path, body)

    def patch(self, path, body):
        return self._answer("PATCH", path, body)


def on(answers: dict) -> FakeClient:
    client = FakeClient(answers)
    remote.use_server(client)
    return client


def test_off_by_default_and_needs_a_signed_in_client():
    assert not remote.active()

    class NoToken(FakeClient):
        token = None

    with pytest.raises(ValueError):
        remote.use_server(NoToken({}))


def test_list_and_get(monkeypatch):
    on({("GET", "/products?include_inactive=true"): [ROW, {**ROW, "barcode": "B2", "name": "Bolt", "is_active": False}],
        ("GET", "/products/a1"): ROW,
        ("GET", "/products/B2"): {**ROW, "barcode": "B2", "is_active": False}})
    monkeypatch.setattr("database.product_repository.connection_scope", None)  # SQLite must not be touched
    products = product_repository.list_all()
    assert [p.barcode for p in products] == ["A1", "B2"]
    assert products[0] == Product("A1", "Anvil", 9.5, 3, 1, True, cost_price=4.0)
    assert product_repository.get_by_barcode("a1").name == "Anvil"
    with pytest.raises(ProductInactiveError):
        product_repository.get_by_barcode("B2", active_only=True)


def test_list_active_filters():
    on({("GET", "/products?include_inactive=false"): [ROW, {**ROW, "barcode": "Z", "is_active": False}]})
    assert [p.barcode for p in product_repository.list_active()] == ["A1"]


def test_create_validates_locally_then_posts():
    client = on({("POST", "/products"): ROW})
    product_repository.create(Product(" a1 ", " Anvil ", 9.499, 0, 1))
    body = client.calls[-1][2]
    assert (body["barcode"], body["name"], body["price"]) == ("A1", "Anvil", 9.5)
    with pytest.raises(UserError):
        product_repository.create(Product("A2", "  ", 1, 0))
    assert len(client.calls) == 1  # the bad one never left the computer


def test_server_refusals_become_the_usual_errors():
    on({("POST", "/products"): ServerError("exists", 409),
        ("PATCH", "/products/NOPE"): ServerError("no", 404),
        ("PATCH", "/products/A1"): ServerError("Your account changed.", 401),
        ("GET", "/products?include_inactive=true"): ServerError("Cannot reach the server (refused).")})
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(Product("A1", "Anvil", 1, 0))
    with pytest.raises(ProductNotFoundError):
        product_repository.set_active("NOPE", False)
    with pytest.raises(SessionInvalidError):
        product_repository.update(Product("A1", "Anvil", 1, 0))
    with pytest.raises(DATABASE_ERRORS) as info:
        product_repository.list_all()
    assert isinstance(info.value, remote.ServerUnavailableError)


def test_update_cost_and_active_send_only_their_fields():
    client = on({("PATCH", "/products/A1"): {**ROW, "cost_price": 2.25}})
    product_repository.update(Product("A1", "Anvil XL", 12, 999, 2))
    assert client.calls[-1][2] == {"name": "Anvil XL", "price": 12.0, "critical_stock_level": 2}  # never stock
    assert product_repository.set_cost("A1", 2.249) == 2.25
    assert client.calls[-1][2] == {"cost_price": 2.25}
    product_repository.set_active("A1", False)
    assert client.calls[-1][2] == {"is_active": False}


def test_delete_is_not_on_the_server_yet():
    client = on({})
    with pytest.raises(remote.NotOnServerYetError):
        product_repository.delete("A1")
    assert client.calls == []


def test_barcodes_are_quoted_in_paths():
    client = on({("GET", "/products/A%2F1%20X"): ROW})
    product_repository.get_by_barcode("A/1 X")
    assert client.calls[-1][1] == "/products/A%2F1%20X"


def test_stock_in_and_out():
    levels = [{"location_kind": "warehouse", "location_code": "WH-01", "product_barcode": "A1", "quantity": 2,
               "updated_at": "2026-11-01T10:00:00Z"}]
    client = on({("POST", "/stock-movements"): {"level_after": 7},
                 ("GET", "/products/A1"): ROW,
                 ("GET", "/stock-levels?product_barcode=A1"): levels})
    assert stock_repository.receive(WH, "A1", 5, note=" supplier ", reference="PO-1") == 7
    body = client.calls[-1][2]
    assert (body["direction"], body["location_code"], body["quantity"], body["note"]) == ("receive", "WH-01", 5,
                                                                                         "supplier")
    with pytest.raises(ValueError):
        stock_repository.dispatch(WH, "A1", 0)
    [level] = stock_repository.levels_for_product("A1")
    assert (level.location, level.quantity, level.product_name) == (WH, 2, "Anvil")


def test_dispatch_too_many_and_unknown_place():
    levels = [{"location_kind": "warehouse", "location_code": "WH-01", "product_barcode": "A1", "quantity": 2,
               "updated_at": "2026-11-01T10:00:00Z"}]
    on({("POST", "/stock-movements"): ServerError("Only 2", 409), ("GET", "/products/A1"): ROW,
        ("GET", "/stock-levels?product_barcode=A1"): levels})
    with pytest.raises(InsufficientStockError) as info:
        stock_repository.dispatch(WH, "A1", 5)
    assert info.value.available == 2
    on({("POST", "/stock-movements"): ServerError("No warehouse", 404), ("GET", "/products/A1"): ROW})
    with pytest.raises(UnknownLocationError):
        stock_repository.receive(StockLocation("warehouse", "WH-99"), "A1", 1)


def test_local_mode_untouched(db_connection, monkeypatch):
    """With server mode off the repositories never reach for a client."""
    def boom(*a, **k):
        raise AssertionError("server called in local mode")

    monkeypatch.setattr(remote, "call", boom)
    with pytest.raises(ProductNotFoundError):
        product_repository.get_by_barcode("NOPE-XYZ")
