"""Deleting a warehouse: open purchase orders block it, and its per-product reorder levels go with it."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import dealership_repository, product_repository, purchase_order_repository as po_repo
from database import stock_repository, warehouse_repository
from database.exceptions import LocationHasOpenOrdersError
from shared.models import Dealership, Product, StockLocation, Warehouse
from tests.po_support import ADMIN, pending_order, seed_people, seed_world, sent_order

WH1 = StockLocation.warehouse("WH-01")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_a_warehouse_with_an_open_order_cannot_be_deleted():
    seed_world()
    seed_people()
    order = sent_order()
    with pytest.raises(LocationHasOpenOrdersError) as excinfo:
        warehouse_repository.delete("WH-01")
    assert "1" in str(excinfo.value) and "WH-01" in str(excinfo.value)
    assert warehouse_repository.get_by_code("WH-01").code == "WH-01"
    po_repo.cancel_order(order.id, ADMIN)
    warehouse_repository.delete("WH-01")  # nothing open any more


def test_a_held_order_also_blocks_and_the_check_survives_a_rename():
    seed_world()
    seed_people()
    pending_order()
    warehouse_repository.update(Warehouse(code="WH-01", name="Gebze", capacity_units=1000, docks=4))
    with pytest.raises(LocationHasOpenOrdersError):
        warehouse_repository.delete("WH-01")


def test_a_recreated_warehouse_does_not_inherit_old_reorder_levels():
    seed_world()
    stock_repository.set_reorder_level(WH1, "PLT-4410", 7)
    assert stock_repository.reorder_overrides_at(WH1) == {"PLT-4410": 7}
    warehouse_repository.delete("WH-01")
    warehouse_repository.create(Warehouse(code="WH-01", name="Again"))
    assert stock_repository.reorder_overrides_at(WH1) == {}


def test_a_recreated_dealership_does_not_inherit_old_reorder_levels():
    shop = StockLocation.dealership("D-A")
    dealership_repository.create(Dealership(code="D-A", name="Shop", region="Metro", city="X"))
    product_repository.create(Product("W", "Widget", 5, 0, 10))
    stock_repository.set_reorder_level(shop, "W", 7)
    dealership_repository.delete("D-A")
    dealership_repository.create(Dealership(code="D-A", name="Shop", region="Metro", city="X"))
    assert stock_repository.reorder_overrides_at(shop) == {}
