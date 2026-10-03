"""Regression tests for database.transaction_repository.

Same _isolated_db redirect pattern as test_product_repository.py - see
that file's docstring for why both the db path AND
database.connection._initialized have to be reset per test.
"""

from __future__ import annotations

import pytest

import database.connection as connection
from database import product_repository, transaction_repository
from database.exceptions import (
    InsufficientStockError,
    ProductNotFoundError,
    TransactionNotFoundError,
)
from shared.models import LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _make_product(barcode: str = "SKU-1", stock: int = 10, **overrides) -> Product:
    fields = dict(barcode=barcode, name="Test Widget", price=5.00, stock_quantity=stock, critical_stock_level=2)
    fields.update(overrides)
    return Product(**fields)


def _pending_sale(*line_items: LineItem) -> Transaction:
    return Transaction(items=list(line_items))


def test_finalize_transaction_deducts_stock():
    product_repository.create(_make_product(stock=10))

    sale = _pending_sale(
        LineItem(product_barcode="SKU-1", product_name_at_sale="Test Widget", unit_price_at_sale=5.00, quantity=3)
    )
    finalized = transaction_repository.finalize_transaction(sale)

    assert finalized.id is not None
    assert finalized.created_at is not None
    assert finalized.total == 15.00

    assert product_repository.get_by_barcode("SKU-1").stock_quantity == 7

    reloaded = transaction_repository.get_by_id(finalized.id)
    assert reloaded.total == 15.00
    assert len(reloaded.items) == 1
    assert reloaded.items[0].product_barcode == "SKU-1"
    assert reloaded.items[0].quantity == 3


def test_finalize_transaction_deducts_multiple_line_items_atomically():
    product_repository.create(_make_product(barcode="A", stock=10))
    product_repository.create(_make_product(barcode="B", stock=10))

    sale = _pending_sale(
        LineItem(product_barcode="A", product_name_at_sale="A", unit_price_at_sale=5.00, quantity=4),
        LineItem(product_barcode="B", product_name_at_sale="B", unit_price_at_sale=5.00, quantity=2),
    )
    transaction_repository.finalize_transaction(sale)

    assert product_repository.get_by_barcode("A").stock_quantity == 6
    assert product_repository.get_by_barcode("B").stock_quantity == 8


def test_finalize_transaction_insufficient_stock_raises_and_writes_nothing():
    product_repository.create(_make_product(barcode="A", stock=10))
    product_repository.create(_make_product(barcode="B", stock=1))

    # B's line item asks for more than is on hand - the whole sale must
    # fail, including A's line item that WOULD have succeeded on its own.
    sale = _pending_sale(
        LineItem(product_barcode="A", product_name_at_sale="A", unit_price_at_sale=5.00, quantity=4),
        LineItem(product_barcode="B", product_name_at_sale="B", unit_price_at_sale=5.00, quantity=5),
    )

    with pytest.raises(InsufficientStockError):
        transaction_repository.finalize_transaction(sale)

    # Neither product's stock moved...
    assert product_repository.get_by_barcode("A").stock_quantity == 10
    assert product_repository.get_by_barcode("B").stock_quantity == 1
    # ...and no transaction/transaction_items row was left behind either.
    with connection.connection_scope(connection.get_db_path()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    assert count == 0


def test_finalize_transaction_unknown_barcode_raises_and_writes_nothing():
    product_repository.create(_make_product(barcode="A", stock=10))

    sale = _pending_sale(
        LineItem(product_barcode="does-not-exist", product_name_at_sale="Ghost", unit_price_at_sale=5.00, quantity=1)
    )

    with pytest.raises(ProductNotFoundError):
        transaction_repository.finalize_transaction(sale)

    with connection.connection_scope(connection.get_db_path()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
    assert count == 0


def test_finalize_transaction_empty_items_raises():
    with pytest.raises(ValueError):
        transaction_repository.finalize_transaction(_pending_sale())


def test_get_by_id_missing_raises():
    with pytest.raises(TransactionNotFoundError):
        transaction_repository.get_by_id(999)
