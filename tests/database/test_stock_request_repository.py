"""Dealership stock requests: POS asks, the depot plans a shipment from the
request or declines it, and "low at dealerships" sees shops running dry."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import database.connection as connection
from database import dealership_repository, product_repository, shipment_repository as ships
from database import stock_repository as stock, stock_request_repository as requests, warehouse_repository
from database.exceptions import (
    DealershipInactiveError,
    DuplicateStockRequestError,
    ProductInactiveError,
    StockRequestStateError,
)
from shared.auth import Actor
from shared.models import Dealership, Product, StockLocation, Warehouse, suggested_request_qty
from tests.stock_invariant import assert_totals_consistent

ETA = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
SHELF = StockLocation.dealership("001")
OTHER = StockLocation.dealership("002")
CASHIER = Actor("B-2", "Selin Kaya")
MANAGER = Actor("M-1", "Murat")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    dealership_repository.create(Dealership(code="002", name="Metro Heavy", region="Metro", city="Columbus"))
    product_repository.create(Product("OIL", "Oil", 10, 100, 10))
    product_repository.create(Product("RICE", "Rice", 4, 100, 5))
    warehouse_repository.create(Warehouse(code="WH-01", name="Merkez", city="Tuzla", capacity_units=5000))
    stock.place_all_unassigned(StockLocation.warehouse("WH-01"))
    stock.transfer(StockLocation.warehouse("WH-01"), SHELF, "OIL", 3)   # 3 <= 10: low
    stock.transfer(StockLocation.warehouse("WH-01"), SHELF, "RICE", 20)  # fine
    stock.transfer(StockLocation.warehouse("WH-01"), OTHER, "RICE", 5)   # 5 <= 5: low


def _ship(request_ids, lines=(("OIL", 17),), dealership="001"):
    return ships.create(origin="WH-01 · Merkez", origin_code="WH-01", dealership_code=dealership,
                        carrier="Ridgeline", eta=ETA, lines=list(lines), request_ids=request_ids, actor=MANAGER)


def test_a_request_snapshots_names_and_who_asked(world):
    request = requests.create("001", "oil", 17, "  for the weekend  ", CASHIER)
    assert request.number == f"RQ-{request.id:05d}"
    assert (request.status, request.product_barcode, request.product_name, request.dealership_name) == (
        "open", "OIL", "Oil", "Harbor Point")
    assert request.note == "for the weekend" and request.requested_by == "Selin Kaya · B-2"
    assert requests.count_open() == 1 and requests.count_open("002") == 0


def test_one_open_request_per_dealership_and_product(world):
    first = requests.create("001", "OIL", 5)
    with pytest.raises(DuplicateStockRequestError) as excinfo:
        requests.create("001", "oil", 7)
    assert first.number in str(excinfo.value)
    requests.create("002", "OIL", 7)  # another shop may ask for the same product
    requests.cancel(first.id, CASHIER)
    requests.create("001", "OIL", 7)  # once withdrawn, ask again


@pytest.mark.parametrize("quantity", [0, -1, 2.5, "x", 100_001])
def test_bad_quantities_are_refused(world, quantity):
    with pytest.raises(ValueError):
        requests.create("001", "OIL", quantity)
    assert requests.list_requests() == []


def test_switched_off_dealership_or_product_cannot_ask(world):
    shop = dealership_repository.get_by_code("002")
    shop.is_active = False
    dealership_repository.update(shop)
    with pytest.raises(DealershipInactiveError):
        requests.create("002", "OIL", 5)
    product_repository.set_active("RICE", False)
    with pytest.raises(ProductInactiveError):
        requests.create("001", "RICE", 5)


def test_planning_a_shipment_from_requests_marks_them_planned_and_delivery_shows_through(world):
    request = requests.create("001", "OIL", 17, actor=CASHIER)
    shipment = _ship([request.id])
    planned = requests.get(request.id)
    assert (planned.status, planned.shipment_id, planned.shipment_status) == ("planned", shipment.id, "scheduled")
    assert planned.decided_by == "Murat · M-1" and not planned.delivered
    ships.dispatch(shipment.id)
    ships.complete_receipt(shipment.id, {})
    assert requests.get(request.id).delivered
    assert stock.quantity_at(SHELF, "OIL") == 20
    assert_totals_consistent()


def test_a_cancelled_shipment_puts_its_requests_back_to_open(world):
    request = requests.create("001", "OIL", 17)
    shipment = _ship([request.id])
    ships.cancel(shipment.id)
    again = requests.get(request.id)
    assert (again.status, again.shipment_id) == ("open", None)


def test_if_the_shop_asked_again_meanwhile_the_old_request_closes_instead(world):
    request = requests.create("001", "OIL", 17)
    shipment = _ship([request.id])
    newer = requests.create("001", "OIL", 4)  # allowed: the first is no longer open
    ships.cancel(shipment.id)
    assert requests.get(request.id).status == "cancelled"
    assert [r.id for r in requests.list_open()] == [newer.id]


def test_a_request_from_another_dealership_or_already_decided_refuses_the_whole_shipment(world):
    mine = requests.create("001", "OIL", 17)
    theirs = requests.create("002", "RICE", 10)
    with pytest.raises(ValueError):
        _ship([mine.id, theirs.id])
    assert ships.list_shipments() == []
    assert requests.get(mine.id).status == "open"
    requests.decline(theirs.id, "No stock until Monday", MANAGER)
    with pytest.raises(StockRequestStateError):
        _ship([theirs.id], lines=[("RICE", 10)], dealership="002")
    assert ships.list_shipments() == []


def test_decline_and_cancel_only_work_on_open_requests(world):
    request = requests.create("001", "OIL", 17)
    declined = requests.decline(request.id, "  Out of season ", MANAGER)
    assert (declined.status, declined.decision_note, declined.decided_by) == ("declined", "Out of season", "Murat · M-1")
    with pytest.raises(StockRequestStateError):
        requests.cancel(request.id)
    with pytest.raises(StockRequestStateError):
        requests.decline(request.id)


def test_list_open_is_oldest_first_and_list_requests_filters(world):
    a = requests.create("001", "OIL", 1)
    b = requests.create("002", "RICE", 2)
    assert [r.id for r in requests.list_open()] == [a.id, b.id]
    assert [r.id for r in requests.list_requests(dealership_code="002")] == [b.id]


def test_dealership_shortages_include_whats_coming_and_asked_for(world):
    rows = {(r.dealership_code, r.product_barcode): r for r in requests.dealership_shortages()}
    assert set(rows) == {("001", "OIL"), ("002", "RICE")}
    oil = rows[("001", "OIL")]
    assert (oil.on_hand, oil.reorder_level, oil.incoming_qty, oil.requested_qty) == (3, 10, 0, 0)
    assert not oil.covered and oil.suggested_qty == 17

    requests.create("002", "RICE", 10)
    shipment = _ship([], lines=[("OIL", 5)])
    rows = {(r.dealership_code, r.product_barcode): r for r in requests.dealership_shortages()}
    assert rows[("001", "OIL")].incoming_qty == 5 and not rows[("001", "OIL")].covered
    assert rows[("002", "RICE")].requested_qty == 10 and rows[("002", "RICE")].covered
    # The uncovered shortage comes first.
    assert requests.dealership_shortages()[0].product_barcode == "OIL"
    assert [r.dealership_code for r in requests.dealership_shortages("002")] == ["002"]
    ships.cancel(shipment.id)


def test_switched_off_dealerships_are_not_in_the_shortage_list(world):
    shop = dealership_repository.get_by_code("002")
    shop.is_active = False
    dealership_repository.update(shop)
    assert {r.dealership_code for r in requests.dealership_shortages()} == {"001"}


def test_suggested_quantity_refills_to_twice_the_reorder_level():
    assert suggested_request_qty(3, 10) == 17
    assert suggested_request_qty(3, 10, already_coming=20) == 0
    assert suggested_request_qty(0, 0) == 0


def test_only_one_open_request_is_enforced_by_the_database_too(world):
    requests.create("001", "OIL", 1)
    with connection.connection_scope() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO stock_requests (dealership_code, dealership_name, product_barcode, "
                         "product_name, quantity) VALUES ('001', 'Harbor Point', 'oil', 'Oil', 2)")
