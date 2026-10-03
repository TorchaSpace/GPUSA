"""Regression tests for database.product_repository.

These exercise the repository FUNCTIONS themselves (not just raw SQL
against the conftest `db_connection` fixture), since that's what every
caller actually uses. product_repository calls database.connection.get_connection()
internally with no explicit db_path, which normally resolves to the real,
machine-wide shared_backend.db via shared.paths.get_db_path() - the
_isolated_db fixture below redirects that to a fresh temp file for the
duration of each test instead.

That redirect has to reset database.connection._initialized too, not
just the path: that flag caches "the schema has already been applied
this process" globally, so without resetting it, only the very FIRST
test in the whole suite to touch a fresh db would actually get schema
applied - every test after it would silently skip schema creation
against ITS OWN fresh (empty) temp file and fail with "no such table:
products".
"""

from __future__ import annotations

import pytest

import database.connection as connection
from database import product_repository
from database.exceptions import DuplicateBarcodeError, ProductNotFoundError
from shared.models import Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _make_product(barcode: str = "1234567890", **overrides) -> Product:
    fields = dict(
        barcode=barcode,
        name="Test Widget",
        price=9.99,
        stock_quantity=10,
        critical_stock_level=3,
    )
    fields.update(overrides)
    return Product(**fields)


def test_create_then_get_by_barcode_roundtrips():
    product = _make_product()

    product_repository.create(product)
    fetched = product_repository.get_by_barcode(product.barcode)

    assert fetched == product


def test_create_duplicate_barcode_raises():
    product_repository.create(_make_product())

    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(_make_product(name="A Different Name"))


def test_get_critical_stock_list_excludes_healthy_stock():
    low = _make_product(barcode="LOW", stock_quantity=1, critical_stock_level=5)
    healthy = _make_product(barcode="HEALTHY", stock_quantity=50, critical_stock_level=5)
    product_repository.create(low)
    product_repository.create(healthy)

    result = product_repository.get_critical_stock_list()

    assert [p.barcode for p in result] == ["LOW"]


def test_get_by_barcode_missing_raises():
    with pytest.raises(ProductNotFoundError):
        product_repository.get_by_barcode("does-not-exist")


def test_update_changes_fields_but_never_stock_quantity():
    original = _make_product()
    product_repository.create(original)

    product_repository.update(
        _make_product(name="Renamed", price=19.99, stock_quantity=999, critical_stock_level=7)
    )

    fetched = product_repository.get_by_barcode(original.barcode)
    assert fetched.name == "Renamed"
    assert fetched.price == 19.99
    assert fetched.critical_stock_level == 7
    # update() never touches stock_quantity - see its docstring - so the
    # 999 passed in above must be silently ignored, not applied.
    assert fetched.stock_quantity == original.stock_quantity


def test_update_missing_raises():
    with pytest.raises(ProductNotFoundError):
        product_repository.update(_make_product(barcode="does-not-exist"))


def test_delete_removes_product():
    product = _make_product()
    product_repository.create(product)

    product_repository.delete(product.barcode)

    with pytest.raises(ProductNotFoundError):
        product_repository.get_by_barcode(product.barcode)


def test_delete_missing_raises():
    with pytest.raises(ProductNotFoundError):
        product_repository.delete("does-not-exist")
