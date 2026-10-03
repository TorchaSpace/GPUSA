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
    LocationInUseError,
    ShipmentNotDispatchedError,
    UnknownLocationError,
    ProductNotFoundError,
    ShipmentNotFoundError,
    ShipmentStateError,
)
from shared.formatting import parse_db_timestamp
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from tests.stock_invariant import assert_totals_consistent

ETA = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)  # always in the future


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


def test_shortfall_is_written_off_and_moves_the_company_total(world):
    shipment = _new()
    ships.dispatch(shipment.id)

    received = ships.complete_receipt(shipment.id, {"BOX-2218": 20, "PLT-4410": 6}, note="4 cartons crushed")

    assert received.receipt_note == "4 cartons crushed"
    assert [(l.product_barcode, l.discrepancy) for l in received.discrepancies] == [("BOX-2218", -4)]
    assert stock.quantity_at(SHELF, "BOX-2218") == 20
    assert stock.quantity_at(SHELF, "PLT-4410") == 6
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 296
    assert product_repository.get_by_barcode("PLT-4410").stock_quantity == 50
    notes = {m["note"] for m in stock.list_movements(location=SHELF) if m["reason"] == "discrepancy"}
    assert any("Short 4" in n for n in notes)
    assert_totals_consistent()


def test_cannot_receive_more_than_was_shipped(world):
    shipment = _new()
    ships.dispatch(shipment.id)

    with pytest.raises(ValueError, match="only 24"):
        ships.complete_receipt(shipment.id, {"BOX-2218": 25})

    assert ships.get(shipment.id).status == "in_transit"  # nothing written
    assert stock.quantity_at(SHELF, "BOX-2218") == 0
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 300
    assert_totals_consistent()


def test_receiving_a_never_dispatched_shipment_is_refused_and_creates_no_stock(world):
    shipment = _new()  # depot forgot to press Dispatch

    with pytest.raises(ShipmentNotDispatchedError):
        ships.complete_receipt(shipment.id, {})

    assert ships.get(shipment.id).status == "scheduled"
    assert stock.quantity_at(WH, "BOX-2218") == 300
    assert stock.quantity_at(SHELF, "BOX-2218") == 0
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 300
    assert_totals_consistent()


def test_received_quantities_must_be_whole_numbers(world):
    shipment = _new()
    ships.dispatch(shipment.id)
    with pytest.raises(ValueError):
        ships.complete_receipt(shipment.id, {"BOX-2218": 2.7})
    assert ships.get(shipment.id).status == "in_transit"


def test_a_shipment_can_only_be_received_once(world):
    shipment = _new()
    ships.dispatch(shipment.id)
    ships.complete_receipt(shipment.id, {"BOX-2218": 20})

    with pytest.raises(ShipmentStateError):
        ships.complete_receipt(shipment.id, {"BOX-2218": 20})
    assert product_repository.get_by_barcode("BOX-2218").stock_quantity == 296  # adjusted once
    assert stock.quantity_at(SHELF, "BOX-2218") == 20


def test_receipt_rejects_unknown_products_and_negatives(world):
    shipment = _new()
    ships.dispatch(shipment.id)
    with pytest.raises(ValueError):
        ships.complete_receipt(shipment.id, {"ZZZ": 1})
    with pytest.raises(ValueError):
        ships.complete_receipt(shipment.id, {"BOX-2218": -1})
    assert ships.get(shipment.id).status == "in_transit"


def test_list_filters_and_incoming_count(world):
    dealership_repository.create(Dealership(code="002", name="Riverbend", region="Valley", city="Boise"))
    a = _new(eta=ETA + timedelta(hours=1))
    b = _new(dealership_code="002", eta=ETA)
    c = _new(origin="WH-02")  # (site text only; stock still from WH-01)
    ships.dispatch(c.id)
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
    ships.cancel(shipment.id)
    dealership_repository.delete("001")
    assert ships.get(shipment.id).dealership_name == "Harbor Point"


# --- validation ---------------------------------------------------------------

@pytest.mark.parametrize("bad", [2.7, 0.5, float("nan"), "3.5", True, -1, 0])
def test_create_rejects_non_whole_or_non_positive_quantities(world, bad):
    with pytest.raises(ValueError):
        _new(lines=[("BOX-2218", bad)])
    assert ships.list_shipments() == []


def test_create_accepts_whole_valued_floats_and_digit_strings(world):
    shipment = _new(lines=[("BOX-2218", 3.0), ("PLT-4410", "4")])
    assert [l.expected_qty for l in shipment.lines] == [3, 4]


def test_create_rejects_an_eta_in_the_past_or_before_departure(world):
    with pytest.raises(ValueError, match="before now"):
        _new(eta=datetime.now(timezone.utc) - timedelta(hours=1))
    with pytest.raises(ValueError, match="departure"):
        _new(eta=ETA, departure=ETA + timedelta(hours=1))
    assert _new(eta=ETA, departure=ETA - timedelta(hours=1)).status == "scheduled"
    assert len(ships.list_shipments()) == 1


def test_update_eta_cannot_precede_the_departure_or_the_planning(world):
    shipment = _new()
    with pytest.raises(ValueError):
        ships.update_eta(shipment.id, datetime.now(timezone.utc) - timedelta(days=1))  # before it was planned
    dispatched = ships.dispatch(shipment.id)
    departed = parse_db_timestamp(dispatched.departed_at)
    with pytest.raises(ValueError, match="departure"):
        ships.update_eta(shipment.id, departed - timedelta(hours=2))
    assert ships.update_eta(shipment.id, departed + timedelta(hours=1)).status == "in_transit"


def test_create_refuses_a_deactivated_product(world):
    from database.exceptions import ProductInactiveError

    product_repository.set_active("BOX-2218", False)
    with pytest.raises(ProductInactiveError):
        _new()
    assert ships.list_shipments() == []


# --- locations -----------------------------------------------------------------

def test_cancel_and_receipt_refuse_a_vanished_location(world):
    """A location deleted behind a shipment's back (a raw delete here, since
    the repositories refuse it) must make cancel/receipt fail, never write
    stock to a place that no longer exists."""
    cancel_me, receive_me = _new(), _new()
    ships.dispatch(cancel_me.id)
    ships.dispatch(receive_me.id)
    with connection.connection_scope() as conn:
        conn.execute("DELETE FROM warehouses WHERE code = 'WH-01'")
        conn.execute("DELETE FROM dealerships WHERE code = '001'")
    with pytest.raises(UnknownLocationError):
        ships.cancel(cancel_me.id)
    with pytest.raises(UnknownLocationError):
        ships.complete_receipt(receive_me.id, {})
    assert ships.get(cancel_me.id).status == "in_transit"
    assert_totals_consistent()


def _empty_the_warehouse():
    for barcode in ("BOX-2218", "PLT-4410"):
        left = stock.quantity_at(WH, barcode)
        if left:
            stock.transfer(WH, UNASSIGNED, barcode, left)


def test_a_warehouse_or_dealership_with_open_shipments_cannot_be_deleted(world):
    shipment = _new()
    _empty_the_warehouse()  # isolates the shipment rule from the has-stock rule

    with pytest.raises(LocationInUseError, match="Deactivate"):
        warehouse_repository.delete("WH-01")
    with pytest.raises(LocationInUseError, match="Deactivate"):
        dealership_repository.delete("001")
    assert warehouse_repository.get_by_code("WH-01") and dealership_repository.get_by_code("001")

    ships.cancel(shipment.id)  # no longer open: both can go
    warehouse_repository.delete("WH-01")
    dealership_repository.delete("001")
    assert ships.get(shipment.id).dealership_name == "Harbor Point"  # history stays


def test_in_transit_shipments_also_block_the_delete(world):
    shipment = _new(lines=[("BOX-2218", 300)])
    ships.dispatch(shipment.id)  # BOX is off the shelf; PLT is still there
    stock.transfer(WH, UNASSIGNED, "PLT-4410", 50)

    with pytest.raises(LocationInUseError):
        warehouse_repository.delete("WH-01")
    with pytest.raises(LocationInUseError):
        dealership_repository.delete("001")

    ships.complete_receipt(shipment.id, {})  # delivered: the warehouse is free to go
    warehouse_repository.delete("WH-01")
