"""Migration v3: products.is_active and the case-insensitive barcode index."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations, product_repository
from database.exceptions import DuplicateBarcodeError
from shared.models import Product

# A database exactly as v2 left it: products WITHOUT is_active, user_version 2.
_V2_DB = """
CREATE TABLE products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL CHECK (price >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    critical_stock_level INTEGER NOT NULL DEFAULT 0 CHECK (critical_stock_level >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')));
INSERT INTO products (barcode, name, price, stock_quantity) VALUES ('box-1', 'Carton', 40, 5), ('TAPE', 'Tape', 5, 0);
PRAGMA user_version = 2;
"""


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _columns(conn):
    return {r[1]: r for r in conn.execute("PRAGMA table_info(products)")}


def test_a_v2_database_gains_is_active_defaulting_to_active(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_V2_DB)
    assert "is_active" not in _columns(old)
    old.close()

    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION >= 3
        assert "is_active" in _columns(conn)
        assert [r[0] for r in conn.execute("SELECT is_active FROM products")] == [1, 1]
        index = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_products_barcode_nocase'").fetchone()
        assert index is not None

    assert all(p.is_active for p in product_repository.list_all())
    # Old mixed-case barcodes are kept as they are, and still found.
    assert product_repository.get_by_barcode("BOX-1").barcode == "box-1"
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(Product("Box-1", "Dup", 1, 0, 0))


def test_migration_is_idempotent(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_V2_DB)
    old.close()
    with connection.connection_scope():
        pass
    connection._initialized = False
    with connection.connection_scope() as conn:
        migrations.run_migrations(conn)
        migrations.run_migrations(conn)
        assert [r[0] for r in conn.execute("SELECT is_active FROM products")] == [1, 1]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION


def test_a_column_already_present_is_not_added_twice(tmp_path):
    """A half-migrated database (column there, version not bumped) upgrades cleanly."""
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_V2_DB)
    old.execute("ALTER TABLE products ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))")
    old.close()
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION


def test_case_only_duplicates_in_an_old_database_do_not_block_startup(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(_V2_DB)
    old.execute("INSERT INTO products (barcode, name, price) VALUES ('BOX-1', 'Carton again', 1)")
    old.commit()
    old.close()
    with connection.connection_scope() as conn:  # must not raise
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_products_barcode_nocase'").fetchone() is None
    # code still refuses NEW case-variants
    with pytest.raises(DuplicateBarcodeError):
        product_repository.create(Product("Tape", "x", 1, 0, 0))


def test_a_fresh_database_has_the_column_and_the_index():
    with connection.connection_scope() as conn:
        assert _columns(conn)["is_active"][4] == "1"  # default
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_products_barcode_nocase'").fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO products (barcode, name, price, is_active) VALUES ('X', 'x', 1, 2)")
