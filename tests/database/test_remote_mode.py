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

    def delete(self, path):
        return self._answer("DELETE", path)


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


def test_product_delete_in_server_mode():
    from database.exceptions import ProductInUseError
    client = on({("DELETE", "/products/A1"): None, ("DELETE", "/products/B2"): ServerError(
        "B2 can't be deleted: it appears on past sales. Deactivate it instead.", 409),
        ("DELETE", "/products/ZZ"): ServerError("No product with barcode ZZ.", 404)})
    product_repository.delete("A1")
    assert client.calls[-1][:2] == ("DELETE", "/products/A1")
    with pytest.raises(ProductInUseError) as used:
        product_repository.delete("B2")
    assert used.value.reason == "it appears on past sales"
    with pytest.raises(ProductNotFoundError):
        product_repository.delete("ZZ")


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


def test_check_cart_reads_the_server_shelf_in_server_mode():
    """The till checks its cart against the server's shelf, not this computer's
    (empty) SQLite file: a product the server sells must not be 'no longer sold'."""
    dealer = StockLocation("dealership", "CST-04")
    row = {"barcode": "A1", "name": "Anvil", "price": 9.5, "quantity": 3, "critical_stock_level": 1,
           "is_active": True, "stocked_here": True}
    gone = {**row, "barcode": "B2", "name": "Bolt", "is_active": False}
    on({("GET", "/stock/products-at?kind=dealership&code=CST-04&include_inactive=true"): [row, gone]})
    assert product_repository.check_cart(dealer, [("a1", 9.5, 3)]) == []  # scanner case
    kinds = {i.barcode: i.kind for i in product_repository.check_cart(
        dealer, [("A1", 9.5, 4), ("B2", 1.0, 1), ("NOPE", 1.0, 1)])}
    assert kinds == {"A1": "stock", "B2": "unavailable", "NOPE": "unavailable"}


def test_floor_receipt_goes_to_the_server_in_server_mode(db_connection):
    """Depot Floor 'Log receipt' in server mode must hit the server (the local file has no such product)."""
    from database import inventory_repository

    client = on({("POST", "/purchase-orders/receive-delivery"): {"orders": []},
                 ("POST", "/stock-movements"): {"level_after": 7}})
    inventory_repository.receive_stock("A1", 4, "note", location=WH, reference="PO-1", bin_code="A-03")
    method, path, body = client.calls[-1]
    assert (method, path) == ("POST", "/purchase-orders/receive-delivery")  # the server settles open orders itself
    assert (body["barcode"], body["quantity"], body["warehouse_code"], body["reference"], body["bin_code"]) == (
        "A1", 4, "WH-01", "PO-1", "A-03")
    inventory_repository.receive_stock("A1", 2, location=StockLocation.dealership("CST-04"))  # not a depot: plain receipt
    assert client.calls[-1][:2] == ("POST", "/stock-movements")


def test_an_unreachable_server_is_not_asked_again_straight_away(monkeypatch):
    """One timeout, then a short pause where calls fail at once - a screen must not freeze on every click."""
    from database.exceptions import DataAccessError

    class Down:
        base_url, token, calls = "http://server.test", "tok", 0

        def get(self, path):
            Down.calls += 1
            raise ServerError("Cannot reach the server (timed out).")

    remote.use_server(Down())
    for _ in range(3):
        with pytest.raises(DataAccessError):
            remote.call("GET", "/stock/movement-totals")
    assert Down.calls == 1  # the other two did not wait for the network
    monkeypatch.setattr(remote, "_down_until", 0.0)  # pause over
    with pytest.raises(DataAccessError):
        remote.call("GET", "/stock/movement-totals")
    assert Down.calls == 2


SHIPMENT = {"id": 7, "origin": "WH-01 · Main", "origin_code": "WH-01", "dealership_code": "CST-04",
            "dealership_name": "Coast", "carrier": "Fast", "driver": None, "status": "scheduled", "departed_at": None,
            "planned_eta": "2026-11-03T10:00:00+00:00", "eta": "2026-11-03T10:00:00+00:00", "delivered_at": None,
            "receipt_note": None, "created_at": "2026-11-02T10:00:00+00:00", "stock_moved": False,
            "lines": [{"id": 1, "product_barcode": "A1", "product_name": "Anvil", "expected_qty": 3,
                       "received_qty": None}]}
REQUEST = {"id": 4, "created_at": "2026-11-02T10:00:00+00:00", "updated_at": "2026-11-02T10:00:00+00:00",
           "dealership_code": "CST-04", "dealership_name": "Coast", "product_barcode": "A1", "product_name": "Anvil",
           "quantity": 5, "note": None, "status": "open", "requested_by": "Ali · B-001", "decided_by": None,
           "decision_note": None, "shipment_id": None, "shipment_status": None}


def test_shipments_in_server_mode(db_connection):
    import datetime as dt

    from database import shipment_repository

    client = on({
        ("POST", "/shipments"): SHIPMENT,
        ("GET", "/shipments?status_in=scheduled%2Cin_transit&dealership_code=CST-04"): [SHIPMENT],
        ("GET", "/shipments/incoming-count?dealership_code=CST-04"): {"count": 1},
        ("GET", "/shipments/7"): SHIPMENT,
        ("GET", "/shipments/8"): ServerError("nope", 404),
        ("POST", "/shipments/7/dispatch"): {**SHIPMENT, "status": "in_transit", "stock_moved": True},
        ("POST", "/shipments/7/eta"): SHIPMENT,
        ("POST", "/shipments/7/cancel"): {**SHIPMENT, "status": "cancelled"},
        ("POST", "/shipments/7/receive"): {**SHIPMENT, "status": "delivered"},
        ("POST", "/shipments/9/dispatch"): ServerError("Only 2 units of A1 at WH-01; 3 cannot leave.", 409),
    })
    made = shipment_repository.create("WH-01 · Main", "CST-04", " Fast ", dt.datetime(2026, 11, 3, 10, 0, tzinfo=dt.timezone.utc),
                                      [("a1", 3)], origin_code="WH-01", request_ids=[4], actor=None)
    assert (made.number, made.eta, made.lines[0].expected_qty, made.status) == ("SH-00007", "2026-11-03T10:00:00.000Z", 3, "scheduled")
    body = client.calls[-1][2]
    assert (body["carrier"], body["lines"], body["request_ids"], body["origin_code"]) == ("Fast", [{"barcode": "a1", "quantity": 3}], [4], "WH-01")
    assert shipment_repository.count_incoming("CST-04") == 1
    assert [s.id for s in shipment_repository.list_shipments(("scheduled", "in_transit"), "CST-04")] == [7]
    with pytest.raises(Exception) as missing:
        shipment_repository.get(8)
    assert type(missing.value).__name__ == "ShipmentNotFoundError"
    assert shipment_repository.dispatch(7).stock_moved is True
    shipment_repository.update_eta(7, dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.timezone.utc))
    assert client.calls[-1][2] == {"eta": "2026-11-04T09:00:00.000Z"}
    assert shipment_repository.cancel(7).status == "cancelled"
    assert shipment_repository.complete_receipt(7, {"A1": 2}, " broke ").status == "delivered"
    assert client.calls[-1][2] == {"received": {"A1": 2}, "note": "broke"}
    with pytest.raises(ValueError):
        shipment_repository.complete_receipt(7, {"A1": -1})
    with pytest.raises(InsufficientStockError) as short:  # the Floor words this one itself
        shipment_repository.dispatch(9)
    assert (short.value.available, short.value.requested, short.value.barcode, short.value.location) == (2, 3, "A1", "WH-01")


def test_stock_requests_in_server_mode(db_connection):
    from database import stock_request_repository as requests

    client = on({
        ("POST", "/stock-requests"): REQUEST,
        ("GET", "/stock-requests?status_in=open&dealership_code=CST-04&limit=50"): [REQUEST],
        ("GET", "/stock-requests/open-count"): {"count": 2},
        ("GET", "/stock-requests/4"): REQUEST,
        ("POST", "/stock-requests/4/cancel"): {**REQUEST, "status": "cancelled"},
        ("POST", "/stock-requests/4/decline"): {**REQUEST, "status": "declined", "decision_note": "none"},
    })
    made = requests.create("CST-04", " a1 ", 5, "  need   them ")
    assert (made.number, made.is_open, made.created_at) == ("RQ-00004", True, "2026-11-02T10:00:00.000Z")
    assert client.calls[-1][2] == {"dealership_code": "CST-04", "barcode": "a1", "quantity": 5, "note": "need them"}
    with pytest.raises(ValueError):
        requests.create("CST-04", "A1", 0)
    assert [r.id for r in requests.list_requests(("open",), "CST-04", 50)] == [4]
    assert requests.count_open() == 2
    assert requests.get(4).product_name == "Anvil"
    assert requests.cancel(4).status == "cancelled"
    assert requests.decline(4, "none").decision_note == "none"
    assert client.calls[-1][2] == {"note": "none"}
    with pytest.raises(Exception):
        requests.create("CST-04", "A1", 1, "x" * 201)


def test_dealership_shortages_in_server_mode(db_connection):
    from database import stock_request_repository as requests

    shop = {"code": "CST-04", "name": "Coast", "region": "Coastal", "city": "Izmir", "manager_name": None, "is_active": True, "id": 1}
    here = {"barcode": "A1", "name": "Anvil", "price": 9.5, "quantity": 1, "critical_stock_level": 5,
            "is_active": True, "stocked_here": True}
    on({
        ("GET", "/dealerships"): [shop],
        ("GET", "/stock/products-at?kind=dealership&code=CST-04"): [here],
        ("GET", "/shipments?status_in=scheduled%2Cin_transit"): [SHIPMENT],
        ("GET", "/stock-requests?status_in=open"): [REQUEST],
    })
    [shortage] = requests.dealership_shortages()
    assert (shortage.on_hand, shortage.reorder_level, shortage.incoming_qty, shortage.requested_qty) == (1, 5, 3, 5)


EMP = {"id": 3, "badge_id": "E-100", "name": "Mia Stone", "title": "Picker", "role": "Operations",
       "location_type": "Warehouse", "location_name": "Main", "is_active": True}
ACC = {"id": 5, "badge_id": "E-100", "name": "Mia Stone", "role": "cashier", "is_active": True, "employee_active": True,
       "location_type": "Warehouse", "location_name": "Main", "failed_attempts": 0, "locked_until": None,
       "last_sign_in_at": "2026-11-02T09:00:00.123456+00:00"}


def test_employees_and_accounts_in_server_mode(db_connection):
    from database import account_repository as accounts
    from database import employee_repository as employees
    from database.exceptions import (
        AccountNotFoundError, DuplicateBadgeIdError, EmployeeNotFoundError, LastAdminError,
    )
    from shared.models import Employee

    client = on({
        ("GET", "/employees"): [EMP],
        ("GET", "/employees/E-100"): EMP,
        ("GET", "/employees/NOPE"): ServerError("none", 404),
        ("GET", "/employees/locations"): {"Warehouse": [{"name": "Main", "code": "WH-01"}], "Dealership": []},
        ("POST", "/employees"): EMP,
        ("PATCH", "/employees/E-100"): EMP,
        ("DELETE", "/employees/E-100"): None,
        ("GET", "/accounts"): [ACC],
        ("GET", "/accounts/without"): [{"badge_id": "E-9", "name": "New"}],
        ("GET", "/accounts/events?limit=5"): [{"created_at": "2026-11-02T09:00:00+00:00", "badge_id": "E-100",
                                               "name": "Mia", "event": "sign_in", "area": "pos", "terminal": "",
                                               "detail": None}],
        ("POST", "/accounts"): ACC,
        ("POST", "/accounts/E-100/pin"): None,
        ("POST", "/accounts/me/pin"): None,
        ("POST", "/accounts/E-100/role"): None,
        ("POST", "/accounts/E-100/active"): ServerError("That is the only active administrator; someone else must be one first.", 409),
        ("POST", "/accounts/E-100/unlock"): None,
        ("DELETE", "/accounts/E-100"): None,
        ("DELETE", "/accounts/NOPE"): ServerError("none", 404),
    })
    assert [e.badge_id for e in employees.list_all()] == ["E-100"]
    assert employees.get_by_badge_id(" e-100 ").name == "Mia Stone"
    with pytest.raises(EmployeeNotFoundError):
        employees.get_by_badge_id("nope")
    assert employees.location_choices() == {"Warehouse": ["Main"], "Dealership": []}
    assert employees.location_choices(include_codes=True)["Warehouse"] == ["Main", "WH-01"]
    new = Employee(badge_id=" e-100 ", name=" Mia   Stone ", title=None, role="Operations",
                   location_type="Warehouse", location_name="Main")
    employees.create(new)
    assert client.calls[-1] == ("POST", "/employees", {"name": "Mia Stone", "title": None, "role": "Operations",
                                                       "location_type": "Warehouse", "location_name": "Main",
                                                       "is_active": True, "badge_id": "E-100"})
    employees.update(new)
    assert client.calls[-1][:2] == ("PATCH", "/employees/E-100")
    employees.delete("e-100")
    assert client.calls[-1][:2] == ("DELETE", "/employees/E-100")
    with pytest.raises(ValueError):
        employees.create(Employee(badge_id="", name="x", title=None, role="Operations",
                                  location_type="Warehouse", location_name="Main"))

    account = accounts.list_accounts()[0]
    assert (account.role, account.last_sign_in_at) == ("cashier", "2026-11-02T09:00:00.123Z")
    assert accounts.get("e-100").id == 5
    with pytest.raises(AccountNotFoundError):
        accounts.get("nobody")
    assert accounts.employees_without_account() == [("E-9", "New")]
    assert accounts.list_events(5)[0]["created_at"] == "2026-11-02T09:00:00.000Z"
    accounts.create_account(" e-100 ", "cashier", "2468")
    assert client.calls[-1][2] == {"badge_id": "E-100", "role": "cashier", "pin": "2468"}
    accounts.set_pin("E-100", "7391")
    assert client.calls[-1][2] == {"pin": "7391"}
    accounts.change_own_pin(None, "2468", "7391")
    assert client.calls[-1][2] == {"current_pin": "2468", "new_pin": "7391"}
    accounts.set_role("E-100", "admin", new_pin="135792")
    assert client.calls[-1][2] == {"role": "admin", "new_pin": "135792"}
    with pytest.raises(LastAdminError):
        accounts.set_active("E-100", False)
    accounts.unlock("E-100")
    accounts.delete("E-100")
    with pytest.raises(AccountNotFoundError):
        accounts.delete("nope")


def test_attendance_in_server_mode(db_connection):
    from database import attendance_repository as attendance
    from database.exceptions import AlreadyCheckedInError, NoOpenAttendanceRecordError

    client = on({
        ("POST", "/attendance/check-in"): {"id": 11},
        ("POST", "/attendance/check-out"): {"id": 11},
        ("GET", "/attendance/open"): [{"badge_id": "E-100", "name": "Mia", "role": "Operations",
                                       "check_in_at": "2026-11-02T08:00:00+00:00"}],
        ("GET", "/attendance/operator?badge_id=E-100&warehouse_code=WH-01"): {"badge_id": "E-100", "name": "Mia"},
        ("POST", "/attendance/never"): None,
    })
    assert attendance.check_in("e-100", "early", warehouse_code="WH-01") == 11
    assert client.calls[-1][2] == {"badge_id": "E-100", "note": "early", "warehouse_code": "WH-01"}
    assert attendance.check_out("E-100") == 11
    assert attendance.list_open()[0]["check_in_at"] == "2026-11-02T08:00:00.000Z"
    assert attendance.operator_actor("e-100", warehouse_code="WH-01").badge_id == "E-100"

    client.answers[("POST", "/attendance/check-in")] = ServerError("E-100 is already checked in.", 409)
    client.answers[("POST", "/attendance/check-out")] = ServerError("E-100 is not checked in.", 409)
    with pytest.raises(AlreadyCheckedInError):
        attendance.check_in("E-100")
    with pytest.raises(NoOpenAttendanceRecordError):
        attendance.check_out("E-100")


def test_roster_in_server_mode(db_connection):
    from datetime import datetime, timezone

    from database import attendance_repository as attendance

    def answer(path):
        return None

    class Roster(FakeClient):
        def get(self, path):
            self.calls.append(("GET", path, None))
            if path == "/employees":
                return [EMP, {**EMP, "id": 4, "badge_id": "E-200", "name": "Zed"}]
            assert path.startswith("/attendance/records?")
            return [{"id": 1, "employee_id": 3, "badge_id": "E-100", "name": "Mia Stone",
                     "check_in_at": "2026-11-02T08:00:00+00:00", "check_out_at": None}]

    remote.use_server(Roster({}))
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    roster = attendance.list_roster("2026-11-02", now=now)
    assert [(r["badge_id"], r["status"]) for r in roster] == [("E-100", "Present"), ("E-200", "Off")]
    events = attendance.list_punches("2026-11-02")
    assert [(e["badge_id"], e["action"]) for e in events] == [("E-100", "IN")]


def test_refunds_and_day_close_in_server_mode(db_connection):
    import datetime as dt

    from database import day_close_repository as closes
    from database import sale_return_repository as returns
    from database import transaction_repository
    from database.exceptions import ReturnApprovalError, ReturnQuantityError, TransactionNotFoundError

    refund = {"id": 6, "transaction_id": 9, "created_at": "2026-11-02T10:00:00+00:00", "dealership_code": "CST-04",
              "payment_method": "cash", "reason": "Broken", "requested_by": "Cy · C-1", "approved_by": "Mo · M-1",
              "client_uuid": "u1u1u1u1", "total": 10.0,
              "lines": [{"barcode": "A1", "name": "Anvil", "unit_price": 5.0, "quantity": 2, "restock": True}]}
    close = {"id": 2, "created_at": "2026-11-02T18:00:00+00:00", "dealership_code": "CST-04",
             "business_date": "2026-11-02", "sales_count": 3, "cash_sales": 30.0, "card_sales": 5.0, "other_sales": 0.0,
             "cash_refunds": 10.0, "card_refunds": 0.0, "expected_cash": 20.0, "counted_cash": 19.0, "difference": -1.0,
             "note": "short", "closed_by": "Cy · C-1"}
    sale = {"id": 9, "client_uuid": "s", "created_at": "2026-11-02T09:00:00+00:00", "total": 15.0,
            "dealership_code": "CST-04", "cashier": "Cy", "payment_method": "cash", "duplicate": False,
            "lines": [{"barcode": "A1", "name": "Anvil", "unit_price": 5.0, "quantity": 3, "unit_cost": 0.0,
                       "cost_known": False}]}
    client = on({
        ("GET", "/sales/9"): sale,
        ("GET", "/sales/8"): ServerError("none", 404),
        ("GET", "/returns/returnable/9"): [{"barcode": "A1", "name": "Anvil", "unit_price": 5.0, "sold": 3, "returned": 1}],
        ("POST", "/returns"): refund,
        ("GET", "/returns/6"): refund,
        ("GET", "/returns?transaction_id=9"): [refund],
        ("POST", "/day-close"): close,
        ("GET", "/day-closes?limit=5&only_differences=false"): [close],
        ("GET", "/day-closes/latest?business_date=2026-11-02&dealership_code=CST-04"): None,
    })
    assert transaction_repository.get_by_id(9).items[0].quantity == 3
    with pytest.raises(TransactionNotFoundError):
        transaction_repository.get_by_id(8)
    line = returns.returnable_lines(9)[0]
    assert (line.sold, line.returned, line.available) == (3, 1, 2)
    made = returns.create_with_approval(9, [("A1", 2, True)], " Broken ", " m-1 ", "2468", "POS refund",
                                        client_uuid="u1u1u1u1")
    assert (made.number, made.total, made.approved_by) == ("RF-00006", 10.0, "Mo · M-1")
    assert client.calls[-1][2] == {"transaction_id": 9, "reason": "Broken", "client_uuid": "u1u1u1u1",
                                   "approver_badge": "m-1", "approver_pin": "2468", "terminal": "POS refund",
                                   "lines": [{"barcode": "A1", "quantity": 2, "restock": True}]}
    assert returns.get(6).reason == "Broken"
    assert [r.id for r in returns.list_for_transaction(9)] == [6]
    with pytest.raises(UserError):
        returns.create_with_approval(9, [("A1", 2, True)], "  ", "m-1", "2468", "POS")
    with pytest.raises(UserError):
        returns.create_with_approval(9, [], "x", "m-1", "2468", "POS")

    client.answers[("POST", "/returns")] = ServerError("A manager must approve a refund.", 403)
    with pytest.raises(ReturnApprovalError):
        returns.create_with_approval(9, [("A1", 1, True)], "x", "c-1", "2468", "POS")
    client.answers[("POST", "/returns")] = ServerError("Only 0 of Anvil can still come back; 1 were asked for.", 409)
    with pytest.raises(ReturnQuantityError) as over:
        returns.create_with_approval(9, [("A1", 1, True)], "x", "m-1", "2468", "POS")
    assert over.value.available == 0

    done = closes.close("CST-04", 19, dt.date(2026, 11, 2), actor=None, note="short")
    assert (done.difference, done.business_date, done.closed_by) == (-1.0, dt.date(2026, 11, 2), "Cy · C-1")
    assert client.calls[-1][2]["counted_cash"] == 19.0 and client.calls[-1][2]["business_date"] == "2026-11-02"
    assert [c.id for c in closes.list_recent(5)] == [2]
    assert closes.latest_for("CST-04", dt.date(2026, 11, 2)) is None
    with pytest.raises(UserError):
        closes.close("CST-04", -1, dt.date(2026, 11, 2))


ORDER = {"id": 7, "product_barcode": "A1", "product_name": "Anvil", "supplier": "Acme", "quantity": 10,
         "unit_price": 5.0, "site": "WH-01 · MAIN", "range_min": 4.0, "range_max": 6.0, "status": "sent",
         "hold_reason": None, "created_at": "2026-11-02T10:00:00+00:00", "decided_at": None, "decision_note": None,
         "raised_by": "Mo · M-1", "decided_by": None, "received_qty": 0, "received_at": None, "received_by": None,
         "cancelled_at": None, "cancelled_by": None}


def test_purchasing_in_server_mode(db_connection):
    from database import purchase_order_repository as orders
    from database.exceptions import (
        ProductNotFoundError, PurchaseOrderAlreadyDecidedError, PurchaseOrderCancelNotAllowedError,
        PurchaseOrderNotFoundError, PurchaseOrderOverReceiveError, PurchaseOrderStateError,
    )
    from shared.auth import Actor
    from shared.models import PriceRange

    band = {"product_barcode": "A1", "min_unit_price": 4.0, "max_unit_price": 6.0, "default_supplier": "Acme"}
    client = on({
        ("GET", "/price-ranges"): [band],
        ("GET", "/price-ranges/A1"): band,
        ("GET", "/price-ranges/ZZ"): None,
        ("PUT", "/price-ranges/A1"): band,
        ("PUT", "/price-ranges/NOPE"): ServerError("none", 404),
        ("DELETE", "/price-ranges/A1"): None,
        ("POST", "/purchase-orders"): ORDER,
        ("GET", "/purchase-orders/7"): ORDER,
        ("GET", "/purchase-orders/8"): ServerError("none", 404),
        ("GET", "/purchase-orders?status=pending&site=WH-01&limit=5"): [ORDER],
        ("GET", "/purchase-orders/pending-ids"): [3, 7],
        ("POST", "/purchase-orders/7/approve"): {**ORDER, "decided_at": "2026-11-02T11:00:00+00:00"},
        ("POST", "/purchase-orders/7/reject"): ServerError("PO-00007 was decided already: it is sent.", 409),
        ("POST", "/purchase-orders/7/receive"): {**ORDER, "status": "partially_received", "received_qty": 4},
        ("POST", "/purchase-orders/receive-delivery"): {"orders": ["PO-00007"]},
        ("POST", "/purchase-orders/7/cancel"): {**ORDER, "status": "cancelled"},
    })
    assert orders.get_price_range("A1").max_unit_price == 6.0
    assert orders.get_price_range("ZZ") is None
    assert [b.product_barcode for b in orders.list_price_ranges()] == ["A1"]
    orders.set_price_range(PriceRange("A1", 4, 6, " Acme "), by=None)
    assert client.calls[-1][2] == {"min_unit_price": 4.0, "max_unit_price": 6.0, "default_supplier": "Acme"}
    with pytest.raises(ProductNotFoundError):
        orders.set_price_range(PriceRange("NOPE", 1, 2))
    with pytest.raises(UserError):
        orders.set_price_range(PriceRange("A1", 7, 3))
    orders.delete_price_range("A1")

    made = orders.submit("a1", " Acme ", 10, 5, "wh-01")
    assert (made.number, made.status, made.created_at, made.raised_by_badge) == (
        "PO-00007", "sent", "2026-11-02T10:00:00.000Z", "M-1")
    assert client.calls[-1][2] == {"barcode": "a1", "supplier": "Acme", "quantity": 10, "unit_price": 5.0, "site": "WH-01"}
    with pytest.raises(UserError):
        orders.submit("A1", "  ", 10, 5, "WH-01")
    with pytest.raises(UserError):
        orders.submit("A1", "Acme", 0, 5, "WH-01")
    assert orders.get(7).total == 50.0
    with pytest.raises(PurchaseOrderNotFoundError):
        orders.get(8)
    assert [o.id for o in orders.list_orders("pending", "WH-01", 5)] == [7]
    assert orders.pending_ids() == [3, 7] and orders.count_pending() == 2

    admin = Actor("B-001", "Ali")
    assert orders.approve(7, " ok ", admin).was_approved
    assert client.calls[-1][2] == {"note": "ok"}
    with pytest.raises(PurchaseOrderAlreadyDecidedError) as decided:
        orders.reject(7, None, admin)
    assert (decided.value.order_number, decided.value.status) == ("PO-00007", "sent")
    with pytest.raises(UserError):
        orders.approve(7, None, None)

    assert orders.receive_against_order(7, 4, admin, WH).status == "partially_received"
    assert client.calls[-1][2] == {"quantity": 4, "warehouse_code": "WH-01"}
    client.answers[("POST", "/purchase-orders/7/receive")] = ServerError("PO-00007 awaits 6 more units; 7 were booked.", 409)
    with pytest.raises(PurchaseOrderOverReceiveError) as over:
        orders.receive_against_order(7, 7, admin, WH)
    assert (over.value.requested, over.value.remaining) == (7, 6)
    client.answers[("POST", "/purchase-orders/7/receive")] = ServerError("PO-00007 is rejected; it cannot be received now.", 409)
    with pytest.raises(PurchaseOrderStateError) as state:
        orders.receive_against_order(7, 1, admin, WH)
    assert (state.value.status, state.value.action) == ("rejected", "received")
    assert orders.receive_delivery(WH, "A1", 9, admin, note="n", reference="DN-1", bin_code="B1") == ["PO-00007"]
    assert client.calls[-1][2]["reference"] == "DN-1"

    assert orders.cancel_order(7, admin).status == "cancelled"
    client.answers[("POST", "/purchase-orders/7/cancel")] = ServerError(
        "Only an administrator can cancel PO-00007 once it is sent.", 403)
    with pytest.raises(PurchaseOrderCancelNotAllowedError) as denied:
        orders.cancel_order(7, admin)
    assert denied.value.reason == "admin_only"


LEDGER = {"id": 4, "direction": "in", "doc_type": "check", "doc_no": "CHK 1", "counterparty": "Acme", "detail": None,
          "site": "WH-01 · MAIN", "issue_date": "2026-11-01", "due_date": "2026-11-20", "amount": 100.0,
          "status": "pending", "settled_at": None, "created_at": "2026-11-02T10:00:00+00:00",
          "created_by": "Ali · B-001", "settled_by": None, "updated_by": "Ali · B-001"}


def test_ledger_in_server_mode(db_connection):
    import datetime as dt
    from database import ledger_repository as ledger
    from database.exceptions import DuplicateLedgerDocumentError, LedgerEntryNotFoundError, LedgerEntryStateError
    from shared.auth import Actor
    from shared.models import LedgerEntry

    audit = {"id": 1, "entry_id": 4, "at": "2026-11-02T10:00:00+00:00", "actor_badge": "B-001", "actor_name": "Ali",
             "action": "created", "before": None, "after": {"doc_no": "CHK 1"}}
    client = on({
        ("POST", "/ledger"): LEDGER,
        ("GET", "/ledger/4"): LEDGER,
        ("GET", "/ledger/5"): ServerError("none", 404),
        ("GET", "/ledger?direction=in&site=WH-01"): [LEDGER],
        ("PUT", "/ledger/4"): {**LEDGER, "counterparty": "Zed"},
        ("DELETE", "/ledger/4"): None,
        ("POST", "/ledger/4/clear"): {**LEDGER, "status": "cleared", "settled_at": "2026-11-02T11:00:00+00:00"},
        ("POST", "/ledger/4/endorse"): ServerError("Only a received check or note can be endorsed.", 422),
        ("POST", "/ledger/4/reopen"): ServerError("Entry 4 is pending; it can't be reopened now.", 409),
        ("GET", "/ledger/4/audit"): [audit],
        ("GET", "/ledger/audit?limit=5"): [audit],
        ("GET", "/ledger/counterparties"): ["Acme"],
    })
    admin = Actor("B-001", "Ali")
    entry = LedgerEntry("in", "check", "chk 1", " Acme ", dt.date(2026, 11, 1), dt.date(2026, 11, 20), 100.0, site="wh-01")
    made = ledger.create(entry, admin)
    assert (made.id, made.doc_no, made.issue_date, made.created_at) == (4, "CHK 1", dt.date(2026, 11, 1), "2026-11-02T10:00:00.000Z")
    assert client.calls[-1][2]["doc_no"] == "CHK 1" and client.calls[-1][2]["site"] == "WH-01"
    with pytest.raises(UserError):
        ledger.create(LedgerEntry("in", "check", " ", "A", dt.date(2026, 11, 1), dt.date(2026, 11, 2), 1), admin)
    with pytest.raises(UserError):
        ledger.create(entry, None)
    assert ledger.get(4).amount == 100.0
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.get(5)
    assert [e.id for e in ledger.list_entries("in", "WH-01")] == [4]
    made.counterparty = "Zed"
    assert ledger.update(made, admin).counterparty == "Zed"
    ledger.delete(4, admin)
    assert ledger.mark_cleared(4, admin).settled_at == "2026-11-02T11:00:00.000Z"
    with pytest.raises(UserError):
        ledger.mark_endorsed(4, admin)
    with pytest.raises(LedgerEntryStateError) as state:
        ledger.reopen(4, admin)
    assert (state.value.status, state.value.action) == ("pending", "reopen")
    client.answers[("POST", "/ledger")] = ServerError("Document CHK 1 is already recorded.", 409)
    with pytest.raises(DuplicateLedgerDocumentError):
        ledger.create(entry, admin)
    history = ledger.list_audit(4)
    assert (history[0].action, history[0].after, history[0].at) == ("created", {"doc_no": "CHK 1"}, "2026-11-02T10:00:00.000Z")
    assert ledger.list_recent_audit(5)[0].actor_badge == "B-001"
    assert ledger.known_counterparties() == ["Acme"]


def test_places_settings_and_audit_in_server_mode(db_connection):
    from database import audit_repository, dealership_repository, settings_repository, warehouse_repository
    from database.exceptions import (
        CapacityBelowUsageError, DealershipNotFoundError, DuplicateDealershipCodeError, DuplicateWarehouseCodeError,
        LocationHasOpenOrdersError, LocationHasStockError, LocationInUseError, WarehouseNotFoundError,
    )
    from shared.models import Dealership, Warehouse
    from shared import store_settings as ss

    client = on({
        ("POST", "/dealerships"): ServerError("Dealership NEW-1 already exists.", 409),
        ("PUT", "/dealerships/NEW-1"): {"code": "NEW-1"},
        ("DELETE", "/dealerships/new-1"): ServerError("NEW-1 still holds 3 units - move them elsewhere before deleting it.", 409),
        ("DELETE", "/dealerships/nope"): ServerError("No dealership NOPE.", 404),
        ("POST", "/warehouses"): {"id": 9, "code": "WH-09"},
        ("PUT", "/warehouses/WH-09"): ServerError("Warehouse WH-09 holds 40 units, over the capacity of 30. Move stock out first.", 409),
        ("DELETE", "/warehouses/WH-09"): ServerError("Warehouse WH-09 has 2 open purchase orders; finish or cancel them first.", 409),
        ("DELETE", "/warehouses/WH-08"): ServerError("Warehouse WH-08 has 1 active shipments; deactivate it instead.", 409),
        ("DELETE", "/warehouses/WH-07"): ServerError("No warehouse WH-07.", 404),
        ("GET", "/settings"): {"ui.language": "tr", "store.name": "Shop"},
        ("GET", "/settings/private"): ServerError("no", 403),
        ("PUT", "/settings"): {},
        ("GET", "/audit?limit=5&kind=product"): [{"id": 3, "at": "2026-11-02T10:00:00+00:00", "actor": "Ali · B-001",
                                                  "action": "deleted", "kind": "product", "target": "A1", "detail": None}],
    })
    shop = Dealership(code=" new-1 ", name=" New ", region="Metro", city="Izmir")
    with pytest.raises(DuplicateDealershipCodeError):
        dealership_repository.create(shop)
    assert client.calls[-1][2]["code"] == "NEW-1"
    with pytest.raises(UserError):
        dealership_repository.create(Dealership(code="x", name=" ", region="Metro", city="c"))
    dealership_repository.update(shop)
    with pytest.raises(LocationHasStockError) as held:
        dealership_repository.delete("new-1")
    assert held.value.args and "3 units" in str(held.value)
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.delete("nope")

    made = warehouse_repository.create(Warehouse("wh-09", "Spare"))
    assert (made.id, made.code) == (9, "WH-09")
    with pytest.raises(CapacityBelowUsageError):
        warehouse_repository.update(Warehouse("WH-09", "Spare", capacity_units=30))
    with pytest.raises(LocationHasOpenOrdersError):
        warehouse_repository.delete("WH-09")
    with pytest.raises(LocationInUseError):
        warehouse_repository.delete("WH-08")
    with pytest.raises(WarehouseNotFoundError):
        warehouse_repository.delete("WH-07")
    client.answers[("POST", "/warehouses")] = ServerError("Warehouse WH-09 already exists.", 409)
    with pytest.raises(DuplicateWarehouseCodeError):
        warehouse_repository.create(Warehouse("WH-09", "Spare"))

    assert settings_repository.safe_language() == "tr"
    assert settings_repository.load_store_profile().name == "Shop"
    settings_repository.save_notifications(ss.NotificationPrefs(low_stock_alerts=False, pending_approvals=True))
    assert client.calls[-1][:2] == ("PUT", "/settings")
    assert client.calls[-1][2] == {"values": {"alerts.low_stock": "0", "alerts.pending_approvals": "1"}}
    entries = audit_repository.list_recent(5, "product")
    assert (entries[0].id, entries[0].target, entries[0].at) == (3, "A1", "2026-11-02T10:00:00.000Z")


def test_activity_feed_and_leftover_reads_in_server_mode(db_connection):
    from database import account_repository, activity_repository, dealership_repository, sale_cost_repository
    from shared.models import Transaction

    ev = {"id": 5, "at": "2026-11-02T10:00:00+00:00", "kind": "sale", "severity": "notice", "source": "pos",
          "location_kind": "dealership", "location_code": "CST-04", "location_name": "Coast", "actor": "Ali",
          "data": {"total": 9}}
    client = on({
        ("GET", "/activity/latest"): {"id": 5},
        ("GET", "/activity/since?after_id=2&limit=50"): [ev],
        ("GET", "/activity/recent?limit=8&min_severity=info"): [ev],
        ("GET", "/activity/recent?limit=3&min_severity=notice&sources=pos&sources=depot"): [ev],
        ("GET", "/activity/count?after_id=2&min_severity=notice"): {"count": 3},
        ("GET", "/dealerships"): [{"id": 1, "code": "CST-04", "name": "Coast", "region": "Coastal", "city": "Izmir",
                                   "manager_name": None, "is_active": True}],
    })
    assert activity_repository.latest_id() == 5
    got = activity_repository.list_since(2, 50)[0]
    assert (got.id, got.kind, got.data, got.location_code) == (5, "sale", {"total": 9}, "CST-04")
    assert [e.id for e in activity_repository.list_recent(8, "info")] == [5]
    assert [e.id for e in activity_repository.list_recent(3, "notice", ("pos", "depot"))] == [5]
    assert activity_repository.count_since(2) == 3
    assert activity_repository.prune() == 0
    assert dealership_repository.canonical_code(" cst-04 ") == "CST-04"
    assert dealership_repository.canonical_code("zz-9") == "ZZ-9"
    assert account_repository.admin_exists() is True
    sales = [Transaction(items=[], id=1)]
    assert sale_cost_repository.attach_costs(sales) is sales
    assert not [c for c in client.calls if c[0] != "GET"]


def test_data_watcher_follows_the_server_counter(qapp):
    from shared.gui_kit.live_updates import DataWatcher

    client = on({("GET", "/changes"): {"version": 10}})
    watcher = DataWatcher(interval_ms=10_000, settle_ms=0, min_gap_ms=0)
    watcher.start()
    assert watcher.check() is False
    client.answers[("GET", "/changes")] = {"version": 11}
    assert watcher.check() is True
    watcher.stop()


def test_recovery_in_server_mode(db_connection, monkeypatch):
    from database import account_repository as accounts
    from database.exceptions import AuthError
    from shared import paths
    from shared.auth import Session

    calls = []

    def fake(self, method, path, body=None):
        calls.append((method, path, body))
        value = fake.answers[(method, path)]
        if isinstance(value, ServerError):
            raise value
        return value

    fake.answers = {("GET", "/recovery/status"): {"has_code": True, "question": "First pet?"},
                    ("POST", "/recovery/reset"): None}
    monkeypatch.setattr("shared.server_client.ServerClient._request", fake)
    monkeypatch.setattr(paths, "get_server_url", lambda: "http://srv")
    assert accounts.has_recovery_code() is True
    assert accounts.get_security_question() == "First pet?"
    accounts.reset_with_recovery_code("k7qd-2mxh-9pra-tc4w", "b-001", "753159", "Till 1")
    assert calls[-1] == ("POST", "/recovery/reset", {"method": "code", "secret": "k7qd-2mxh-9pra-tc4w",
                                                     "badge_id": "b-001", "new_pin": "753159", "terminal": "Till 1"})
    with pytest.raises(ValueError):
        accounts.reset_with_security_answer("fistik", "b-001", "123", "Till 1")  # weak PIN: stopped before the server
    for status, key in ((404, None), (401, None), (423, None), (403, None)):
        fake.answers[("POST", "/recovery/reset")] = ServerError(
            "Recovery is locked until 2026-11-02T10:00:00+00:00." if status == 423 else "no", status)
        with pytest.raises(AuthError):
            accounts.reset_with_security_answer("fistik", "b-001", "753159", "Till 1")

    on({("POST", "/recovery/code"): {"code": "AAAA-BBBB-CCCC-DDDD"}, ("PUT", "/recovery/question"): None})
    admin = Session(account_id=1, badge_id="B-001", name="Ali", role="admin", signed_in_at="2026-11-02T10:00:00.000Z",
                    area="admin", terminal="Admin")
    assert accounts.create_recovery_code(admin) == "AAAA-BBBB-CCCC-DDDD"
    accounts.set_security_question(admin, "753159", "Name of my first pet?", "Fistik")
    remote.use_server(None)


def test_server_required_blocks_local_sign_in(monkeypatch):
    from database import account_repository as accounts
    from shared import paths

    monkeypatch.setattr(paths, "get_server_url", lambda: None)
    monkeypatch.setenv(paths.SERVER_REQUIRED_ENV, "1")
    assert paths.server_required() is True
    with pytest.raises(remote.ServerUnavailableError):
        accounts.authenticate("B-001", "753159", "admin", "Admin")
    monkeypatch.setenv(paths.SERVER_REQUIRED_ENV, "0")
    assert paths.server_required() is False


def test_server_address_skips_the_first_administrator_screen(monkeypatch):
    """With a server address the local file is only a cache: an empty one must not offer to create a
    "first administrator" there, who could never sign in to the server."""
    from database import account_repository as accounts
    from shared import paths

    monkeypatch.setattr(paths, "get_server_url", lambda: None)
    assert accounts.admin_exists() is False  # fresh local file, no server: the set-up screen
    monkeypatch.setattr(paths, "get_server_url", lambda: "http://srv")
    assert accounts.admin_exists() is True   # server installation: straight to sign-in
