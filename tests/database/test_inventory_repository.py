"""Regression tests for database.inventory_repository - mirrors
test_transaction_repository.py's shape/fixture pattern.
"""

from __future__ import annotations

import pytest

import database.connection as connection
from database import inventory_repository, product_repository
from database.exceptions import InsufficientStockError, ProductNotFoundError
from shared.models import Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _make_product(barcode: str = "SKU-1", **overrides) -> Product:
    fields = dict(barcode=barcode, name="Pallet Wrap", price=5.0, stock_quantity=10, critical_stock_level=3)
    fields.update(overrides)
    return Product(**fields)


def test_receive_stock_increases_quantity_and_logs_movement():
    product_repository.create(_make_product(stock_quantity=10))

    inventory_repository.receive_stock("SKU-1", 40, note="PO-20931")

    updated = product_repository.get_by_barcode("SKU-1")
    assert updated.stock_quantity == 50

    movements = inventory_repository.list_recent_movements()
    assert len(movements) == 1
    assert movements[0]["movement_type"] == "receive"
    assert movements[0]["quantity"] == 40
    assert movements[0]["note"] == "PO-20931"
    assert movements[0]["product_name"] == "Pallet Wrap"


def test_dispatch_stock_decreases_quantity_and_logs_movement():
    product_repository.create(_make_product(stock_quantity=50))

    inventory_repository.dispatch_stock("SKU-1", 20, note="SO-58812")

    updated = product_repository.get_by_barcode("SKU-1")
    assert updated.stock_quantity == 30

    movements = inventory_repository.list_recent_movements(movement_type="dispatch")
    assert len(movements) == 1
    assert movements[0]["quantity"] == 20


def test_dispatch_stock_insufficient_raises_and_writes_nothing():
    product_repository.create(_make_product(stock_quantity=5))

    with pytest.raises(InsufficientStockError):
        inventory_repository.dispatch_stock("SKU-1", 6)

    assert product_repository.get_by_barcode("SKU-1").stock_quantity == 5
    assert inventory_repository.list_recent_movements() == []


def test_receive_unknown_barcode_raises():
    with pytest.raises(ProductNotFoundError):
        inventory_repository.receive_stock("does-not-exist", 10)


def test_dispatch_unknown_barcode_raises():
    with pytest.raises(ProductNotFoundError):
        inventory_repository.dispatch_stock("does-not-exist", 10)


def test_non_positive_quantity_raises_value_error():
    product_repository.create(_make_product())

    with pytest.raises(ValueError):
        inventory_repository.receive_stock("SKU-1", 0)
    with pytest.raises(ValueError):
        inventory_repository.dispatch_stock("SKU-1", -5)


def test_list_recent_movements_orders_newest_first_and_filters_by_type():
    product_repository.create(_make_product(stock_quantity=100))

    inventory_repository.receive_stock("SKU-1", 10)
    inventory_repository.dispatch_stock("SKU-1", 5)
    inventory_repository.receive_stock("SKU-1", 20)

    all_moves = inventory_repository.list_recent_movements()
    assert [m["movement_type"] for m in all_moves] == ["receive", "dispatch", "receive"]
    assert [m["quantity"] for m in all_moves] == [20, 5, 10]

    receives_only = inventory_repository.list_recent_movements(movement_type="receive")
    assert all(m["movement_type"] == "receive" for m in receives_only)
    assert len(receives_only) == 2


def test_list_recent_movements_invalid_type_raises():
    with pytest.raises(ValueError):
        inventory_repository.list_recent_movements(movement_type="sale")
