"""A sale carries a key from the till (migration v9): sending the same sale
twice stores it once and takes the stock off once."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations, product_repository, transaction_repository
from shared.models import LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _sale(uuid: str | None = None, quantity: int = 3) -> Transaction:
    line = LineItem("SKU-1", "Widget", 5.00, quantity)
    return Transaction(items=[line], client_uuid=uuid)


def _stock() -> int:
    return product_repository.get_by_barcode("SKU-1").stock_quantity


def test_the_same_key_sells_once_and_returns_the_stored_sale():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 10, 2))
    first = transaction_repository.finalize_transaction(_sale("till-a-0001"))
    again = transaction_repository.finalize_transaction(_sale("till-a-0001"))
    assert again.id == first.id and again.client_uuid == "till-a-0001"
    assert _stock() == 7
    assert len(transaction_repository.list_between(first.created_at.replace(year=2000),
                                                   first.created_at.replace(year=2100))) == 1


def test_a_retry_is_not_refused_even_when_the_stock_has_since_run_out():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 3, 2))
    first = transaction_repository.finalize_transaction(_sale("k1"))
    assert _stock() == 0
    assert transaction_repository.finalize_transaction(_sale("k1")).id == first.id  # not InsufficientStock


def test_a_sale_without_a_key_gets_one_and_two_different_keys_are_two_sales():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 10, 2))
    a = transaction_repository.finalize_transaction(_sale())
    b = transaction_repository.finalize_transaction(_sale())
    assert a.client_uuid and b.client_uuid and a.client_uuid != b.client_uuid
    assert a.id != b.id and _stock() == 4
    assert transaction_repository.get_by_id(a.id).client_uuid == a.client_uuid


def test_the_database_itself_refuses_a_duplicate_key():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 10, 2))
    transaction_repository.finalize_transaction(_sale("dup"))
    with connection.connection_scope() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO transactions (total, client_uuid) VALUES (1, 'dup')")


def test_a_v8_database_gains_the_column_and_the_unique_index(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT "
        "(strftime('%Y-%m-%dT%H:%M:%fZ','now')), total REAL NOT NULL, dealership_code TEXT, cashier TEXT, "
        "payment_method TEXT); INSERT INTO transactions (total) VALUES (9); PRAGMA user_version = 8;")
    old.close()
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert "client_uuid" in {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
        assert conn.execute("SELECT client_uuid FROM transactions").fetchone()[0] is None  # old sale untouched
        assert any(r[1] == "idx_transactions_client_uuid" for r in conn.execute("PRAGMA index_list(transactions)"))
