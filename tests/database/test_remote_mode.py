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

    client = on({("POST", "/stock-movements"): {"level_after": 7}})
    inventory_repository.receive_stock("A1", 4, "note", location=WH, reference="PO-1", bin_code="A-03")
    method, path, body = client.calls[-1]
    assert (method, path) == ("POST", "/stock-movements")
    assert (body["direction"], body["barcode"], body["quantity"], body["location_code"]) == ("receive", "A1", 4, "WH-01")


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
