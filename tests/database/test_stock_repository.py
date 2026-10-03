"""Per-location stock: database.stock_repository, warehouse_repository,
the per-location sale in transaction_repository, and migrations.py."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations
from database import (
    dealership_repository,
    inventory_repository,
    product_repository,
    stock_repository as stock,
    transaction_repository,
    warehouse_repository,
)
from database.exceptions import (
    DuplicateWarehouseCodeError,
    InsufficientStockError,
    LocationHasStockError,
    ProductNotFoundError,
    UnknownLocationError,
    WarehouseNotFoundError,
)
from shared.models import UNASSIGNED, Dealership, LineItem, Product, StockLocation, Transaction, Warehouse
from tests.stock_invariant import assert_totals_consistent

WH1 = StockLocation.warehouse("WH-01")
WH2 = StockLocation.warehouse("WH-02")
SHOP = StockLocation.dealership("001")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    warehouse_repository.create(Warehouse(code="WH-01", name="İstanbul Merkez", city="Tuzla", capacity_units=1000, docks=8))
    warehouse_repository.create(Warehouse(code="WH-02", name="Ankara", city="Sincan"))
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("BOX", "Carton", 40, 100, 10))
    product_repository.create(Product("TAPE", "Tape", 5, 0, 3))


def test_location_model_rules():
    assert UNASSIGNED.label == "Unassigned" and WH1.label == "WH-01"
    with pytest.raises(ValueError):
        StockLocation("warehouse")
    with pytest.raises(ValueError):
        StockLocation("unassigned", "X")
    with pytest.raises(ValueError):
        StockLocation("shelf", "X")


def test_new_product_stock_starts_unassigned(world):
    assert stock.quantity_at(UNASSIGNED, "BOX") == 100
    assert stock.units_by_location() == {UNASSIGNED: 100}
    assert_totals_consistent()


def test_receive_and_dispatch_at_a_warehouse_move_the_total(world):
    stock.receive(WH1, "TAPE", 40, note="PO-7")
    stock.dispatch(WH1, "TAPE", 5, note="damaged")

    assert stock.quantity_at(WH1, "TAPE") == 35
    assert product_repository.get_by_barcode("TAPE").stock_quantity == 35
    [out, inn] = stock.list_movements(location=WH1)
    assert (out["movement_type"], out["reason"], out["note"], out["location"]) == ("dispatch", "dispatch", "damaged", WH1)
    assert (inn["movement_type"], inn["quantity"]) == ("receive", 40)
    assert_totals_consistent()


def test_dispatch_is_limited_to_what_that_location_holds(world):
    stock.receive(WH1, "TAPE", 4)
    stock.receive(WH2, "TAPE", 50)
    with pytest.raises(InsufficientStockError) as info:
        stock.dispatch(WH1, "TAPE", 5)
    assert (info.value.available, info.value.location) == (4, "WH-01")
    assert stock.quantity_at(WH1, "TAPE") == 4


def test_unknown_locations_and_products_are_rejected(world):
    with pytest.raises(UnknownLocationError):
        stock.receive(StockLocation.warehouse("WH-99"), "BOX", 1)
    with pytest.raises(UnknownLocationError):
        stock.receive(StockLocation.dealership("999"), "BOX", 1)
    with pytest.raises(ProductNotFoundError):
        stock.receive(WH1, "NOPE", 1)
    with pytest.raises(ValueError):
        stock.receive(WH1, "BOX", 0)
    assert stock.units_by_location() == {UNASSIGNED: 100}


def test_transfer_moves_between_places_without_changing_the_total(world):
    stock.transfer(UNASSIGNED, WH1, "BOX", 60, note="put away")
    stock.transfer(WH1, SHOP, "BOX", 10)

    assert stock.quantity_at(UNASSIGNED, "BOX") == 40
    assert stock.quantity_at(WH1, "BOX") == 50
    assert stock.quantity_at(SHOP, "BOX") == 10
    assert product_repository.get_by_barcode("BOX").stock_quantity == 100
    references = {(m["location"], m["movement_type"], m["reference"]) for m in stock.list_movements() if m["reason"] == "transfer"}
    assert (WH1, "dispatch", "001") in references and (SHOP, "receive", "WH-01") in references
    with pytest.raises(InsufficientStockError):
        stock.transfer(SHOP, WH2, "BOX", 11)
    with pytest.raises(ValueError):
        stock.transfer(WH1, WH1, "BOX", 1)
    assert_totals_consistent()


def test_count_sets_the_level_and_logs_the_difference(world):
    stock.transfer(UNASSIGNED, WH1, "BOX", 100)
    assert stock.set_count(WH1, "BOX", 97) == -3
    assert stock.set_count(WH1, "BOX", 97) == 0
    assert stock.set_count(WH1, "BOX", 99, note="found behind rack") == 2

    assert stock.quantity_at(WH1, "BOX") == 99
    assert product_repository.get_by_barcode("BOX").stock_quantity == 99
    counts = [m for m in stock.list_movements(location=WH1) if m["reason"] == "count"]
    assert [(m["movement_type"], m["quantity"]) for m in counts] == [("receive", 2), ("dispatch", 3)]
    assert counts[0]["note"] == "found behind rack" and counts[1]["note"] == "Counted 97"
    with pytest.raises(ValueError):
        stock.set_count(WH1, "BOX", -1)
    assert_totals_consistent()


def test_place_all_unassigned(world):
    stock.receive(UNASSIGNED, "TAPE", 7)
    assert stock.place_all_unassigned(WH2) == 107
    assert stock.units_by_location() == {WH2: 107}
    assert stock.place_all_unassigned(WH2) == 0
    with pytest.raises(ValueError):
        stock.place_all_unassigned(UNASSIGNED)
    assert_totals_consistent()


def test_local_product_views(world):
    stock.transfer(UNASSIGNED, SHOP, "BOX", 8)  # critical level 10
    local = {p.barcode: p.stock_quantity for p in stock.products_at(SHOP)}
    assert local == {"BOX": 8, "TAPE": 0}
    # TAPE (critical level 3, none on hand) is NOT flagged: this shop has never stocked it.
    assert [p.barcode for p in stock.critical_at(SHOP)] == ["BOX"]
    assert {p.barcode: p.stocked_here for p in stock.products_at(SHOP)} == {"BOX": True, "TAPE": False}
    assert stock.product_at(SHOP, "BOX").stock_quantity == 8
    assert [(lvl.location, lvl.quantity) for lvl in stock.levels_for_product("BOX")] == [(UNASSIGNED, 92), (SHOP, 8)]
    assert [lvl.product_barcode for lvl in stock.levels_at(SHOP)] == ["BOX"]


def test_inventory_facade_uses_the_given_location(world):
    inventory_repository.receive_stock("TAPE", 9, "in", location=WH1)
    inventory_repository.dispatch_stock("TAPE", 2, "out", location=WH1)
    assert stock.quantity_at(WH1, "TAPE") == 7
    assert [m["movement_type"] for m in inventory_repository.list_recent_movements(location=WH1)] == ["dispatch", "receive"]


def test_movement_totals_since(world):
    stock.receive(WH1, "TAPE", 9)
    stock.dispatch(WH1, "TAPE", 2)
    stock.transfer(UNASSIGNED, WH2, "BOX", 5)
    totals = stock.movement_totals_since("2000-01-01T00:00:00")
    assert totals[WH1] == (9, 2) and totals[WH2] == (5, 0) and totals[UNASSIGNED] == (100, 5)  # 100 = BOX's initial stock
    assert stock.movement_totals_since("2999-01-01T00:00:00") == {}


# --- sales come off the dealership's shelf ----------------------------------

def _sale(barcode="BOX", qty=3, price=40.0):
    return Transaction(items=[LineItem(barcode, "Carton", price, qty)])


def test_sale_deducts_from_the_selling_dealership(world):
    stock.transfer(UNASSIGNED, SHOP, "BOX", 5)
    done = transaction_repository.finalize_transaction(_sale(qty=3), location=SHOP)

    assert done.dealership_code == "001"
    assert transaction_repository.get_by_id(done.id).dealership_code == "001"
    assert stock.quantity_at(SHOP, "BOX") == 2
    assert stock.quantity_at(UNASSIGNED, "BOX") == 95  # other stock untouched
    assert product_repository.get_by_barcode("BOX").stock_quantity == 97
    assert_totals_consistent()


def test_sale_fails_when_the_shelf_is_short_even_if_the_company_has_stock(world):
    stock.transfer(UNASSIGNED, SHOP, "BOX", 2)
    with pytest.raises(InsufficientStockError) as info:
        transaction_repository.finalize_transaction(_sale(qty=3), location=SHOP)
    assert (info.value.available, info.value.location) == (2, "001")
    assert stock.quantity_at(SHOP, "BOX") == 2


def test_same_product_on_two_lines_is_checked_as_a_sum(world):
    stock.transfer(UNASSIGNED, SHOP, "BOX", 4)
    sale = Transaction(items=[LineItem("BOX", "Carton", 40, 3), LineItem("BOX", "Carton", 40, 3)])
    with pytest.raises(InsufficientStockError):
        transaction_repository.finalize_transaction(sale, location=SHOP)
    assert stock.quantity_at(SHOP, "BOX") == 4


def test_sale_without_identity_uses_unassigned(world):
    done = transaction_repository.finalize_transaction(_sale(qty=1))
    assert done.dealership_code is None
    assert stock.quantity_at(UNASSIGNED, "BOX") == 99


def test_sale_at_an_unregistered_dealership_is_refused(world):
    with pytest.raises(UnknownLocationError):
        transaction_repository.finalize_transaction(_sale(qty=1), location=StockLocation.dealership("777"))


# --- warehouses -------------------------------------------------------------

def test_warehouse_crud_and_validation(world):
    w = warehouse_repository.get_by_code("WH-01")
    assert (w.site_label, w.capacity_units, w.docks) == ("WH-01 · İstanbul Merkez", 1000, 8)
    w.capacity_units, w.city = None, "Pendik"
    warehouse_repository.update(w)
    assert warehouse_repository.get_by_code("WH-01").capacity_units is None
    with pytest.raises(DuplicateWarehouseCodeError):
        warehouse_repository.create(Warehouse(code="WH-01", name="Again"))
    for bad in (Warehouse(code=" ", name="x"), Warehouse(code="A", name=""), Warehouse(code="A", name="x", capacity_units=0),
                Warehouse(code="A", name="x", docks=-1)):
        with pytest.raises(ValueError):
            warehouse_repository.create(bad)
    with pytest.raises(WarehouseNotFoundError):
        warehouse_repository.update(Warehouse(code="NOPE", name="x"))
    assert [x.code for x in warehouse_repository.list_all()] == ["WH-01", "WH-02"]


def test_a_warehouse_holding_stock_cant_be_deleted(world):
    stock.transfer(UNASSIGNED, WH2, "BOX", 1)
    with pytest.raises(LocationHasStockError):
        warehouse_repository.delete("WH-02")
    stock.transfer(WH2, WH1, "BOX", 1)
    warehouse_repository.delete("WH-02")
    with pytest.raises(WarehouseNotFoundError):
        warehouse_repository.get_by_code("WH-02")


def test_ensure_default_creates_wh01_only_when_there_is_none():
    first = warehouse_repository.ensure_default()
    assert (first.code, first.name, first.capacity_units) == ("WH-01", "Main warehouse", None)
    assert warehouse_repository.ensure_default().code == "WH-01"
    assert len(warehouse_repository.list_all()) == 1


def test_ensure_default_prefers_an_existing_active_warehouse():
    warehouse_repository.create(Warehouse(code="AN-3", name="Ankara"))
    assert warehouse_repository.ensure_default().code == "AN-3"


# --- upgrading a database from before per-location stock --------------------

_OLD_TABLES = """
CREATE TABLE products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL CHECK (price >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    critical_stock_level INTEGER NOT NULL DEFAULT 0 CHECK (critical_stock_level >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));
CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), total REAL NOT NULL CHECK (total >= 0));
CREATE TABLE stock_movements (id INTEGER PRIMARY KEY AUTOINCREMENT, product_barcode TEXT NOT NULL REFERENCES products(barcode),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('receive', 'dispatch')), quantity INTEGER NOT NULL CHECK (quantity > 0),
    note TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));
CREATE TABLE shipments (id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, dealership_code TEXT NOT NULL,
    dealership_name TEXT NOT NULL, carrier TEXT NOT NULL, driver TEXT, status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled', 'in_transit', 'delivered', 'cancelled')), departed_at TEXT, planned_eta TEXT NOT NULL,
    eta TEXT NOT NULL, delivered_at TEXT, receipt_note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));
CREATE TABLE shipment_lines (id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    product_barcode TEXT NOT NULL REFERENCES products(barcode), product_name_at_ship TEXT NOT NULL,
    expected_qty INTEGER NOT NULL CHECK (expected_qty > 0), received_qty INTEGER CHECK (received_qty >= 0),
    UNIQUE (shipment_id, product_barcode));
INSERT INTO products (barcode, name, price, stock_quantity) VALUES ('BOX', 'Carton', 40, 300), ('TAPE', 'Tape', 5, 0);
INSERT INTO stock_movements (product_barcode, movement_type, quantity, note) VALUES ('BOX', 'receive', 300, 'old');
INSERT INTO shipments (id, origin, dealership_code, dealership_name, carrier, status, planned_eta, eta)
    VALUES (1, 'WH-01 · İstanbul Merkez', '001', 'Harbor Point', 'X', 'in_transit', '2026-09-26T15:00:00Z', '2026-09-26T15:00:00Z'),
           (2, 'WH-01 · İstanbul Merkez', '001', 'Harbor Point', 'X', 'scheduled', '2026-09-26T15:00:00Z', '2026-09-26T15:00:00Z');
INSERT INTO shipment_lines (shipment_id, product_barcode, product_name_at_ship, expected_qty) VALUES (1, 'BOX', 'Carton', 24), (2, 'BOX', 'Carton', 10);
"""


def test_old_database_is_upgraded_once(tmp_path):
    path = tmp_path / "t.db"
    old = sqlite3.connect(path)
    old.executescript(_OLD_TABLES)
    old.close()

    # Existing stock minus what's on the road becomes unassigned.
    assert stock.quantity_at(UNASSIGNED, "BOX") == 276
    assert stock.quantity_at(UNASSIGNED, "TAPE") == 0
    [legacy] = inventory_repository.list_recent_movements()
    assert legacy["location"] is None and legacy["note"] == "old"
    assert_totals_consistent()

    # A second app starting against the same file doesn't redo it.
    connection._initialized = False
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
    assert stock.quantity_at(UNASSIGNED, "BOX") == 276

    # The road shipment arrives: it was already off everyone's stock.
    from database import shipment_repository
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    assert shipment_repository.get(1).stock_moved and not shipment_repository.get(2).stock_moved
    shipment_repository.complete_receipt(1, {})
    assert stock.quantity_at(SHOP, "BOX") == 24
    assert product_repository.get_by_barcode("BOX").stock_quantity == 300
    # The planned one (no origin_code) draws from unassigned on dispatch.
    shipment_repository.dispatch(2)
    assert stock.quantity_at(UNASSIGNED, "BOX") == 266
    assert_totals_consistent()


def test_a_dealership_with_stock_on_its_shelves_cant_be_deleted(world):
    stock.transfer(UNASSIGNED, SHOP, "BOX", 2)
    with pytest.raises(LocationHasStockError):
        dealership_repository.delete("001")
    stock.set_count(SHOP, "BOX", 0)
    dealership_repository.delete("001")
