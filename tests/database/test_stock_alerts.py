"""Low/out events also fire when stock leaves a warehouse by Floor dispatch, transfer, distribution
or a count (not only by a sale or a shipment load)."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import activity_repository, product_repository, stock_repository as stock, warehouse_repository
from shared.models import UNASSIGNED, Product, StockLocation, Warehouse
from tests.stock_invariant import assert_totals_consistent

WH1, WH2 = StockLocation.warehouse("WH-01"), StockLocation.warehouse("WH-02")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    warehouse_repository.create(Warehouse(code="WH-01", name="Merkez"))
    warehouse_repository.create(Warehouse(code="WH-02", name="Izmir"))
    product_repository.create(Product("W", "Widget", 5, 20, 10))
    stock.place_all_unassigned(WH1)  # 20 at WH-01, reorder level 10


def _since(after):
    return [(e.kind, e.location_code) for e in activity_repository.list_since(after)]


def test_dispatch_to_low_then_staying_low_is_quiet():
    after = activity_repository.latest_id()
    stock.dispatch(WH1, "W", 15)  # 20 -> 5, under 10
    assert ("stock_low", "WH-01") in _since(after)
    after = activity_repository.latest_id()
    stock.dispatch(WH1, "W", 1)  # still low: no repeat
    assert "stock_low" not in [k for k, _ in _since(after)]
    assert_totals_consistent()


def test_transfer_out_of_a_warehouse_to_zero_is_stock_out():
    after = activity_repository.latest_id()
    stock.transfer(WH1, WH2, "W", 20)
    assert ("stock_out", "WH-01") in _since(after)
    assert_totals_consistent()


def test_a_count_that_finds_nothing_is_stock_out():
    after = activity_repository.latest_id()
    stock.set_count(WH1, "W", 0)
    assert ("stock_out", "WH-01") in _since(after)
    assert_totals_consistent()


def test_a_count_that_finds_more_is_quiet():
    stock.dispatch(WH1, "W", 15)
    after = activity_repository.latest_id()
    stock.set_count(WH1, "W", 50)
    assert not [e for e in _since(after) if e[0] in ("stock_low", "stock_out")]


def test_distribute_alerts_once_for_the_source():
    after = activity_repository.latest_id()
    stock.distribute(WH1, "W", [(WH2, 8), (UNASSIGNED, 7)])  # 20 -> 5
    assert [e for e in _since(after) if e[0] in ("stock_low", "stock_out")] == [("stock_low", "WH-01")]
    assert_totals_consistent()


def test_placing_the_unplaced_pile_raises_no_alert():
    product_repository.create(Product("X", "Other", 5, 3, 10))
    after = activity_repository.latest_id()
    stock.place_all_unassigned(WH2)
    assert not [e for e in _since(after) if e[0] in ("stock_low", "stock_out")]
