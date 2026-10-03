"""Warehouse code normalisation, capacity rules, inactive warehouses and
whole-number quantities in stock_repository."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import dealership_repository, migrations, product_repository, stock_repository as stock, warehouse_repository
from database.exceptions import (
    CapacityBelowUsageError,
    CapacityExceededError,
    DuplicateWarehouseCodeError,
    LocationInactiveError,
    WarehouseNotFoundError,
)
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from tests.stock_invariant import assert_totals_consistent

WH = StockLocation.warehouse("WH-01")
WH2 = StockLocation.warehouse("WH-02")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    warehouse_repository.create(Warehouse(code="WH-01", name="Main", capacity_units=100))
    warehouse_repository.create(Warehouse(code="WH-02", name="Spare"))
    product_repository.create(Product("A", "Widget", 5, 0, 0))


# --- codes -------------------------------------------------------------------

def test_codes_are_stored_trimmed_and_upper_case_by_create_and_update():
    created = warehouse_repository.create(Warehouse(code="  wh-07 ", name="Seven"))
    assert created.code == "WH-07"
    assert warehouse_repository.get_by_code("wh-07").code == "WH-07"
    warehouse_repository.update(Warehouse(code=" wh-07", name="Seven renamed"))
    assert [(w.code, w.name) for w in warehouse_repository.list_all()] == [("WH-07", "Seven renamed")]


def test_a_code_that_differs_only_by_case_is_a_duplicate():
    warehouse_repository.create(Warehouse(code="WH-01", name="One"))
    with pytest.raises(DuplicateWarehouseCodeError):
        warehouse_repository.create(Warehouse(code="wh-01", name="Again"))


def test_blank_code_and_fractional_numbers_are_rejected():
    for bad in (Warehouse(code="  ", name="x"), Warehouse(code="A", name="x", capacity_units=10.5),
                Warehouse(code="A", name="x", docks=2.5), Warehouse(code="A", name="x", capacity_units=0)):
        with pytest.raises(ValueError):
            warehouse_repository.create(bad)
    assert warehouse_repository.list_all() == []


def test_update_of_an_unknown_code():
    with pytest.raises(WarehouseNotFoundError):
        warehouse_repository.update(Warehouse(code="NOPE", name="x"))


def test_a_legacy_lower_case_code_can_still_be_found_and_edited(tmp_path):
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO warehouses (code, name) VALUES ('old-1', 'Legacy')")
    assert warehouse_repository.get_by_code("OLD-1").code == "old-1"
    warehouse_repository.update(Warehouse(code="old-1", name="Edited"))
    assert warehouse_repository.get_by_code("old-1").name == "Edited"
    warehouse_repository.delete("OLD-1")
    assert warehouse_repository.list_all() == []


# --- capacity ------------------------------------------------------------------

def test_lowering_capacity_below_current_usage_is_refused(world):
    stock.receive(WH, "A", 60)
    with pytest.raises(CapacityBelowUsageError, match="60"):
        warehouse_repository.update(Warehouse(code="WH-01", name="Main", capacity_units=59))
    assert warehouse_repository.get_by_code("WH-01").capacity_units == 100  # unchanged
    warehouse_repository.update(Warehouse(code="WH-01", name="Main", capacity_units=60))  # exactly full is fine
    warehouse_repository.update(Warehouse(code="WH-01", name="Main", capacity_units=None))  # unlimited is fine


def test_receiving_past_capacity_is_refused_and_writes_nothing(world):
    stock.receive(WH, "A", 90)
    with pytest.raises(CapacityExceededError, match="exceed"):
        stock.receive(WH, "A", 11)
    assert stock.quantity_at(WH, "A") == 90
    assert stock.receive(WH, "A", 10) == 100  # exactly full is fine
    assert len(stock.list_movements(location=WH)) == 2
    assert_totals_consistent()


def test_transfers_and_placing_unassigned_stock_respect_capacity(world):
    stock.receive(UNASSIGNED, "A", 150)
    with pytest.raises(CapacityExceededError):
        stock.transfer(UNASSIGNED, WH, "A", 101)
    stock.transfer(UNASSIGNED, WH, "A", 100)
    with pytest.raises(CapacityExceededError):
        stock.transfer(UNASSIGNED, WH, "A", 1)
    with pytest.raises(CapacityExceededError):
        stock.place_all_unassigned(WH)
    assert stock.place_all_unassigned(WH2) == 50  # no capacity set: unlimited
    assert_totals_consistent()


def test_a_warehouse_without_capacity_takes_anything(world):
    assert stock.receive(WH2, "A", 10_000) == 10_000


def test_receiving_into_an_inactive_warehouse_is_refused(world):
    w = warehouse_repository.get_by_code("WH-01")
    w.is_active = False
    warehouse_repository.update(w)
    with pytest.raises(LocationInactiveError):
        stock.receive(WH, "A", 1)
    stock.receive(UNASSIGNED, "A", 5)
    with pytest.raises(LocationInactiveError):
        stock.transfer(UNASSIGNED, WH, "A", 1)
    with pytest.raises(LocationInactiveError):
        stock.place_all_unassigned(WH)
    assert stock.quantity_at(WH, "A") == 0


def test_stock_can_still_leave_an_inactive_warehouse(world):
    stock.receive(WH, "A", 10)
    w = warehouse_repository.get_by_code("WH-01")
    w.is_active = False
    warehouse_repository.update(w)
    stock.transfer(WH, UNASSIGNED, "A", 4)
    stock.dispatch(WH, "A", 6)
    assert stock.quantity_at(WH, "A") == 0
    assert_totals_consistent()


# --- whole numbers -----------------------------------------------------------------

@pytest.mark.parametrize("bad", [2.7, 0.2, float("nan"), "1.5", True, 0, -3])
def test_quantities_must_be_whole_and_positive(world, bad):
    with pytest.raises(ValueError):
        stock.receive(WH, "A", bad)
    with pytest.raises(ValueError):
        stock.dispatch(WH, "A", bad)
    with pytest.raises(ValueError):
        stock.transfer(UNASSIGNED, WH, "A", bad)
    assert stock.list_movements() == []


def test_a_count_must_be_a_whole_number(world):
    with pytest.raises(ValueError):
        stock.set_count(WH, "A", 3.5)
    assert stock.set_count(WH, "A", 4) == 4
