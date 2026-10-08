"""Each shop can have its own reorder level for a product (set in Admin); sales alert against that."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import activity_repository, dealership_repository, product_repository, stock_repository, transaction_repository
from shared.models import UNASSIGNED, Dealership, LineItem, Product, StockLocation, Transaction

SHOP_A, SHOP_B = StockLocation.dealership("D-A"), StockLocation.dealership("D-B")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    for code in ("D-A", "D-B"):
        dealership_repository.create(Dealership(code=code, name=f"Shop {code}", region="Metro", city="X"))
    product_repository.create(Product("W", "Widget", 5, 100, 10))
    stock_repository.distribute(UNASSIGNED, "W", [(SHOP_A, 40), (SHOP_B, 40)])


def _level(shop):
    return next(p for p in stock_repository.products_at(shop) if p.barcode == "W").critical_stock_level


def test_a_shop_uses_the_product_level_until_admin_sets_its_own():
    assert _level(SHOP_A) == 10 and _level(SHOP_B) == 10
    stock_repository.set_reorder_level(SHOP_A, "W", 30)
    assert _level(SHOP_A) == 30 and _level(SHOP_B) == 10
    assert [p.barcode for p in stock_repository.critical_at(SHOP_A)] == []  # 40 on hand > 30
    stock_repository.set_reorder_level(SHOP_A, "W", 45)
    assert [p.barcode for p in stock_repository.critical_at(SHOP_A)] == ["W"]
    assert stock_repository.critical_at(SHOP_B) == []
    stock_repository.set_reorder_level(SHOP_A, "W", None)  # back to the product's own
    assert _level(SHOP_A) == 10


def test_zero_means_never_alert_here_and_bad_values_are_refused():
    stock_repository.set_reorder_level(SHOP_B, "W", 0)
    assert _level(SHOP_B) == 0 and stock_repository.critical_at(SHOP_B) == []
    with pytest.raises(Exception):
        stock_repository.set_reorder_level(SHOP_B, "W", -1)
    with pytest.raises(Exception):
        stock_repository.set_reorder_level(UNASSIGNED, "W", 5)


def test_a_sale_raises_the_low_stock_event_at_the_shops_own_level():
    stock_repository.set_reorder_level(SHOP_A, "W", 35)  # product default 10 would stay quiet
    after = activity_repository.latest_id()
    transaction_repository.finalize_transaction(Transaction(items=[LineItem("W", "Widget", 5, 6)]), location=SHOP_A)  # 34 left
    kinds = [(e.kind, e.location_code) for e in activity_repository.list_since(after)]
    assert ("stock_low", "D-A") in kinds
    after = activity_repository.latest_id()
    transaction_repository.finalize_transaction(Transaction(items=[LineItem("W", "Widget", 5, 6)]), location=SHOP_B)  # 34 left, level 10
    assert "stock_low" not in [e.kind for e in activity_repository.list_since(after)]
