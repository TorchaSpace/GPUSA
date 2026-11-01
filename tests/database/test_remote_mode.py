"""Server mode (database/remote.py): repository calls go to the API, answers
come back as the usual models and exceptions, and local mode is untouched."""

from __future__ import annotations

import pytest

from database import (product_repository, purchase_order_repository, remote, sale_return_repository,
                      shipment_repository, stock_repository)
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


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Server reads are cached in the local file: keep that in a temp one."""
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


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

    def put(self, path, body):
        return self._answer("PUT", path, body)


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
               "product_name": "Anvil", "updated_at": "2026-11-01T10:00:00Z"}]
    client = on({("POST", "/stock-movements"): {"level_after": 7},
                 ("GET", "/products/A1"): ROW,
                 ("GET", "/stock-levels?location_kind=warehouse&location_code=WH-01&product_barcode=A1"): levels,
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
               "product_name": "Anvil", "updated_at": "2026-11-01T10:00:00Z"}]
    on({("POST", "/stock-movements"): ServerError("Only 2", 409), ("GET", "/products/A1"): ROW,
        ("GET", "/stock-levels?location_kind=warehouse&location_code=WH-01&product_barcode=A1"): levels,
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


def test_stock_reads_and_writes_per_place():
    lv = {"location_kind": "dealership", "location_code": "CST-04", "product_barcode": "A1", "product_name": "Anvil",
          "quantity": 3, "updated_at": "2026-11-01T10:00:00Z"}
    here = {"barcode": "A1", "name": "Anvil", "price": 9.5, "quantity": 3, "critical_stock_level": 5,
            "is_active": True, "stocked_here": True}
    mv = {"id": 9, "created_at": "2026-11-01T10:00:00.123456+00:00", "direction": "receive", "barcode": "A1",
          "product_name": "Anvil", "location_kind": "warehouse", "location_code": "WH-01", "quantity": 4,
          "note": None, "reason": "transfer", "reference": "CST-04", "handled_by": "Ali · B-001", "bin_code": None}
    dealer = StockLocation("dealership", "CST-04")
    client = on({
        ("POST", "/stock/transfers"): {"moved": 4, "source_level_after": 0},
        ("POST", "/stock/counts"): {"difference": -1},
        ("POST", "/stock/unassigned/place"): {"moved": 6},
        ("POST", "/stock/unassigned/clear"): {"removed": 2},
        ("PUT", "/stock/reorder-levels"): {"barcode": "A1", "level": 5},
        ("GET", "/stock/reorder-levels?kind=dealership&code=CST-04"): {"A1": 5},
        ("GET", "/stock/products-at?kind=dealership&code=CST-04"): [here],
        ("GET", "/stock/products-at?kind=dealership&code=CST-04&include_inactive=true"): [here],
        ("GET", "/stock-levels?location_kind=dealership&location_code=CST-04"): [lv],
        ("GET", "/stock-levels"): [lv, {**lv, "quantity": 0}],
        ("GET", "/stock/units-by-location"): [{"location_kind": "dealership", "location_code": "CST-04", "units": 3}],
        ("GET", "/stock/in-transit"): {"units": 0},
        ("GET", "/stock-movements?location_kind=warehouse&location_code=WH-01&limit=50"): [mv],
        ("GET", "/stock/movement-totals?since=2026-11-01T00%3A00%3A00.000Z"):
            [{"location_kind": "warehouse", "location_code": "WH-01", "units_in": 4, "units_out": 0}],
        ("GET", "/stock/last-moved?kind=warehouse&code=WH-01"): {"A1": "2026-11-01T10:00:00+00:00"},
    })
    stock_repository.transfer(WH, dealer, "A1", 4, note=" move ")
    assert client.calls[-1][2] == {"barcode": "A1", "source": {"kind": "warehouse", "code": "WH-01"}, "note": "move",
                                   "legs": [{"kind": "dealership", "code": "CST-04", "quantity": 4}]}
    with pytest.raises(UserError):
        stock_repository.transfer(WH, WH, "A1", 1)  # checked before anything is sent
    assert stock_repository.set_count(dealer, "A1", 2) == -1
    assert stock_repository.place_all_unassigned(WH) == 6
    assert stock_repository.clear_unassigned() == 2
    stock_repository.set_reorder_level(dealer, "A1", 5)
    assert stock_repository.reorder_overrides_at(dealer) == {"A1": 5}
    [p] = stock_repository.products_at(dealer)
    assert (p.stock_quantity, p.critical_stock_level, p.stocked_here) == (3, 5, True)
    assert stock_repository.critical_at(dealer) == [p]
    assert stock_repository.product_at(dealer, "a1").barcode == "A1"
    assert [lv.quantity for lv in stock_repository.levels_at(dealer)] == [3]
    assert len(stock_repository.all_levels()) == 1  # zero levels left out
    assert stock_repository.units_by_location() == {dealer: 3}
    assert stock_repository.units_in_transit() == 0
    [m] = stock_repository.list_movements(location=WH)
    assert (m["created_at"], m["location"], m["movement_type"]) == ("2026-11-01T10:00:00.123Z", WH, "receive")
    assert stock_repository.movement_totals_since("2026-11-01T00:00:00.000Z") == {WH: (4, 0)}
    assert stock_repository.last_movement_at(WH) == {"A1": "2026-11-01T10:00:00.000Z"}


def test_places_read_from_the_server_and_writes_wait():
    from database import dealership_repository, warehouse_repository
    from shared.models import Warehouse

    on({("GET", "/warehouses"): [{"id": 1, "code": "WH-01", "name": "Main", "city": "", "capacity_units": 10,
                                  "docks": 2, "is_active": True},
                                 {"id": 2, "code": "WH-02", "name": "Old", "city": "", "capacity_units": None,
                                  "docks": 0, "is_active": False}],
        ("GET", "/dealerships"): [{"id": 1, "code": "CST-04", "name": "Coast", "region": "Coastal", "city": "Izmir",
                                   "manager_name": None, "is_active": True}]})
    assert [w.code for w in warehouse_repository.list_all(active_only=True)] == ["WH-01"]
    assert warehouse_repository.get_by_code("wh-02").name == "Old"
    assert dealership_repository.get_by_code(" cst-04 ").city == "Izmir"
    with pytest.raises(remote.NotOnServerYetError):
        warehouse_repository.create(Warehouse("WH-03", "New"))


@pytest.mark.parametrize(
    "call",
    [
        lambda: shipment_repository.create("Main", "D-1", "Truck", None, [("A1", 1)]),
        lambda: shipment_repository.dispatch(1),
        lambda: shipment_repository.cancel(1),
        lambda: shipment_repository.complete_receipt(1, {"A1": 1}),
        lambda: purchase_order_repository.receive_delivery(None, "A1", 1),
        lambda: purchase_order_repository.receive_against_order(1, 1, None),
        lambda: sale_return_repository.create(1, [("A1", 1, True)], "damaged", None),
    ],
)
def test_stock_changes_without_a_server_version_are_refused(call):
    """Shipments, order receipts and returns still write this computer's own stock tables; in server mode
    they must refuse instead of pulling the local stock away from the server's."""
    client = on({})
    with pytest.raises(remote.NotOnServerYetError):
        call()
    assert client.calls == []
