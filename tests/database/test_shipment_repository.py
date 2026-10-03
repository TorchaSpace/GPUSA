"""Regression tests for database.shipment_repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships
from database import stock_repository as stock, warehouse_repository
from database.exceptions import (
    DealershipNotFoundError,
    InsufficientStockError,
    UnknownLocationError,
    ProductNotFoundError,
    ShipmentNotFoundError,
    ShipmentStateError,
)
from shared.formatting import parse_db_timestamp
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from tests.stock_invariant import assert_totals_consistent

ETA = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    product_repository.create(Product("BOX-2218", "Carton", 40, 300, 10))
    product_repository.create(Product("PLT-4410", "Pallet wrap", 900, 50, 10))
    warehouse_repository.create(Warehouse(code="WH-01", name="İstanbul Merkez", city="Tuzla", capacity_units=5400))
    stock.place_all_unassigned(StockLocation.warehouse("WH-01"))


def _new(**overrides):
    fields = dict(origin="WH-01 · İstanbul Merkez", origin_code="WH-01", dealership_code="001", carrier="Ridgeline Freight", eta=ETA,
                  lines=[("BOX-2218", 24), ("PLT-4410", 6)], driver="Murat Y.")
    fields.update(overrides)
    return ships.create(**fields)


def test_create_snapshots_names_and_starts_scheduled(world):
    shipment = _new()

    assert shipment.number == f"SH-{shipment.id:05d}"
    assert shipment.status == "scheduled"
    assert shipment.dealership_name == "Harbor Point"
    assert parse_db_timestamp(shipment.eta) == ETA
    assert shipment.planned_eta == shipment.eta
    assert [(l.product_barcode, l.product_name, l.expected_qty, l.received_qty) for l in shipment.lines] == [
        ("BOX-2218", "Carton", 24, None),
        ("PLT-4410", "Pallet wrap", 6, None),
    ]
    assert shipment.item_count == 30


def test_duplicate_product_lines_are_merged(world):
    shipment = _new(lines=[("BOX-2218", 10), ("BOX-2218", 5)])
    assert [(l.product_barcode, l.expected_qty) for l in shipment.lines] == [("BOX-2218", 15)]


def test_creating_touches_no_stock(world):
    _new()
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 300
    assert stock.quantity_at(StockLocation.warehouse("WH-01"), "BOX-2218") == 300


@pytest.mark.parametrize(
    "overrides, error",
    [
        (dict(carrier=" "), ValueError),
        (dict(origin=""), ValueError),
        (dict(lines=[]), ValueError),
        (dict(lines=[("BOX-2218", 0)]), ValueError),
        (dict(dealership_code="NOPE"), DealershipNotFoundError),
        (dict(lines=[("NOPE", 1)]), ProductNotFoundError),
        (dict(origin_code="WH-99"), UnknownLocationError),
    ],
)
def test_create_validates(world, overrides, error):
    with pytest.raises(error):
        _new(**overrides)
    assert ships.list_shipments() == []


def test_dispatch_update_eta_and_cancel(world):
    shipment = _new()

    dispatched = ships.dispatch(shipment.id)
    assert dispatched.status == "in_transit" and dispatched.departed_at

    later = ETA + timedelta(hours=2, minutes=50)
    moved = ships.update_eta(shipment.id, later)
    assert parse_db_timestamp(moved.eta) == later
    assert parse_db_timestamp(moved.planned_eta) == ETA  # the promise is kept

    with pytest.raises(ShipmentStateError):
        ships.dispatch(shipment.id)  # already on the road

    assert ships.cancel(shipment.id).status == "cancelled"
    with pytest.raises(ShipmentStateError):
        ships.update_eta(shipment.id, later)


WH = StockLocation.warehouse("WH-01")
SHELF = StockLocation.dealership("001")


def test_legacy_shipment_without_origin_code_draws_from_unassigned(world):
    product_repository.create(Product("LBL-0091", "Labels", 450, 8, 1))  # starts unassigned
    shipment = ships.create(origin="Old site", dealership_code="001", carrier="X", eta=ETA, lines=[("LBL-0091", 5)])
    ships.dispatch(shipment.id)
    assert stock.quantity_at(UNASSIGNED, "LBL-0091") == 3
    assert_totals_consistent()


def test_dispatch_takes_stock_off_the_origin_but_keeps_the_company_total(world):
    shipment = _new()
    ships.dispatch(shipment.id)

    assert stock.quantity_at(WH, "BOX-2218") == 276
    assert stock.quantity_at(SHELF, "BOX-2218") == 0
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 300  # on the truck
    moved = [m for m in stock.list_movements(location=WH) if m["reason"] == "shipment"]
    assert {(m["barcode"], m["movement_type"], m["reason"], m["reference"]) for m in moved} == {
        ("BOX-2218", "dispatch", "shipment", shipment.number),
        ("PLT-4410", "dispatch", "shipment", shipment.number),
    }
    assert_totals_consistent()


def test_dispatch_fails_whole_when_the_origin_is_short(world):
    shipment = _new(lines=[("BOX-2218", 5), ("PLT-4410", 51)])  # only 50 wrap at WH-01
    with pytest.raises(InsufficientStockError) as info:
        ships.dispatch(shipment.id)
    assert info.value.location == "WH-01"
    assert ships.get(shipment.id).status == "scheduled"
    assert stock.quantity_at(WH, "BOX-2218") == 300  # nothing taken
    assert_totals_consistent()


def test_cancelling_a_dispatched_shipment_puts_the_goods_back(world):
    shipment = _new()
    ships.dispatch(shipment.id)
    ships.cancel(shipment.id)
    assert stock.quantity_at(WH, "BOX-2218") == 300
    assert stock.quantity_at(WH, "PLT-4410") == 50
    assert_totals_consistent()


def test_receipt_in_full_puts_everything_on_the_dealership_shelf(world):
    shipment = _new()
    ships.dispatch(shipment.id)

    received = ships.complete_receipt(shipment.id, {})

    assert received.status == "delivered" and received.delivered_at
    assert [l.received_qty for l in received.lines] == [24, 6]
    assert received.discrepancies == []
    assert stock.quantity_at(SHELF, "BOX-2218") == 24
    assert stock.quantity_at(WH, "BOX-2218") == 276
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 300
    assert not [m for m in stock.list_movements() if m["reason"] == "discrepancy"]
    assert_totals_consistent()


def test_shortfall_and_surplus_adjust_company_stock(world):
    shipment = _new()
    ships.dispatch(shipment.id)

    received = ships.complete_receipt(shipment.id, {"BOX-2218": 20, "PLT-4410": 7}, note="4 cartons crushed")

    assert received.receipt_note == "4 cartons crushed"
    assert [(l.product_barcode, l.discrepancy) for l in received.discrepancies] == [("BOX-2218", -4), ("PLT-4410", 1)]
    assert stock.quantity_at(SHELF, "BOX-2218") == 20
    assert stock.quantity_at(SHELF, "PLT-4410") == 7
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 296
    assert product_repository.get_by_barcode("PLT-4410").stock_quantity == 51
    notes = {m["note"] for m in stock.list_movements(location=SHELF) if m["reason"] == "discrepancy"}
    assert any("Short 4" in n for n in notes)
    assert any("Over by 1" in n for n in notes)
    assert_totals_consistent()


def test_receiving_a_never_dispatched_shipment_takes_it_off_the_origin(world):
    shipment = _new()  # depot forgot to press Dispatch
    received = ships.complete_receipt(shipment.id, {})
    assert received.departed_at is not None
    assert stock.quantity_at(WH, "BOX-2218") == 276
    assert stock.quantity_at(SHELF, "BOX-2218") == 24
    assert_totals_consistent()


def test_never_dispatched_receipt_when_origin_records_are_short(world):
    product_repository.create(Product("LBL-0091", "Labels", 450, 2, 10))
    stock.place_all_unassigned(WH)  # only 2 on record at WH-01
    shipment = _new(lines=[("LBL-0091", 6)])

    ships.complete_receipt(shipment.id, {})  # 6 arrived anyway

    assert stock.quantity_at(WH, "LBL-0091") == 0
    assert stock.quantity_at(SHELF, "LBL-0091") == 6
    assert product_repository.get_by_barcode("LBL-0091").stock_quantity == 6  # 4 more than was on record
    [extra] = [m for m in stock.list_movements(location=SHELF) if m["reason"] == "discrepancy"]
    assert extra["quantity"] == 4 and "had only 2 of 6" in extra["note"]
    assert_totals_consistent()


def test_a_shipment_can_only_be_received_once(world):
    shipment = _new()
    ships.complete_receipt(shipment.id, {"BOX-2218": 20})

    with pytest.raises(ShipmentStateError):
        ships.complete_receipt(shipment.id, {"BOX-2218": 20})
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 296  # adjusted once
    assert stock.quantity_at(SHELF, "BOX-2218") == 20


def test_receipt_rejects_unknown_products_and_negatives(world):
    shipment = _new()
    with pytest.raises(ValueError):
        ships.complete_receipt(shipment.id, {"ZZZ": 1})
    with pytest.raises(ValueError):
        ships.complete_receipt(shipment.id, {"BOX-2218": -1})
    assert ships.get(shipment.id).status == "scheduled"


def test_list_filters_and_incoming_count(world):
    dealership_repository.create(Dealership(code="002", name="Riverbend", region="Valley", city="Boise"))
    a = _new(eta=ETA + timedelta(hours=1))
    b = _new(dealership_code="002", eta=ETA)
    c = _new(origin="WH-02")  # (site text only; stock still from WH-01)
    ships.complete_receipt(c.id, {})

    assert [s.id for s in ships.list_shipments()] == [b.id, c.id, a.id]  # soonest ETA first
    assert [s.id for s in ships.list_shipments(dealership_code="002")] == [b.id]
    assert [s.id for s in ships.list_shipments(origin="WH-02")] == [c.id]
    assert [s.id for s in ships.list_shipments(statuses=("scheduled",))] == [b.id, a.id]
    assert ships.count_incoming("001") == 1
    assert ships.count_incoming(None) == 2


def test_missing_shipment():
    with pytest.raises(ShipmentNotFoundError):
        ships.get(1)
    with pytest.raises(ShipmentNotFoundError):
        ships.dispatch(1)


def test_deleting_the_dealership_keeps_shipment_history(world):
    shipment = _new()
    dealership_repository.delete("001")
    assert ships.get(shipment.id).dealership_name == "Harbor Point"
