"""Migration v4: purchase-order receiving states/columns (a table rebuild),
products.cost_price and the transaction_items cost snapshot columns."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations, product_repository, purchase_order_repository as po_repo
from database.connection import _SCHEMA_PATH
from shared.models import Product

# A database exactly as v3 left it: the old three-state purchase_orders, products
# without cost_price, transaction_items without the cost snapshot.
_V3_DB = """
CREATE TABLE products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL CHECK (price >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    critical_stock_level INTEGER NOT NULL DEFAULT 0 CHECK (critical_stock_level >= 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)));
CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    total REAL NOT NULL CHECK (total >= 0), dealership_code TEXT, cashier TEXT);
CREATE TABLE transaction_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    product_barcode TEXT NOT NULL REFERENCES products(barcode), product_name_at_sale TEXT NOT NULL,
    unit_price_at_sale REAL NOT NULL CHECK (unit_price_at_sale >= 0), quantity INTEGER NOT NULL CHECK (quantity > 0));
CREATE TABLE purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_barcode TEXT NOT NULL REFERENCES products(barcode),
    product_name_at_order TEXT NOT NULL, supplier TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0), unit_price REAL NOT NULL CHECK (unit_price > 0),
    site TEXT NOT NULL, range_min REAL, range_max REAL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'rejected')),
    hold_reason TEXT CHECK (hold_reason IN ('above_range', 'below_range', 'no_range')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at TEXT, decision_note TEXT, raised_by TEXT, decided_by TEXT);
CREATE INDEX idx_purchase_orders_status ON purchase_orders(status);
INSERT INTO products (barcode, name, price, stock_quantity) VALUES ('PLT', 'Pallet wrap', 900, 5), ('BOX', 'Carton', 40, 0);
INSERT INTO transactions (id, total) VALUES (1, 90);
INSERT INTO transaction_items (transaction_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity)
    VALUES (1, 'PLT', 'Pallet wrap', 900, 1);
INSERT INTO purchase_orders (id, product_barcode, product_name_at_order, supplier, quantity, unit_price, site,
    range_min, range_max, status, hold_reason, created_at, decided_at, decision_note, raised_by, decided_by) VALUES
  (1, 'PLT', 'Pallet wrap', 'Kuzey', 40, 742.5, 'WH-01', 520, 680, 'pending', 'above_range', '2026-09-01T08:00:00.000Z', NULL, NULL, 'Deniz · D-1', NULL),
  (2, 'PLT', 'Pallet wrap', 'Kuzey', 10, 600, 'WH-01', 520, 680, 'sent', NULL, '2026-09-02T08:00:00.000Z', NULL, NULL, 'Deniz · D-1', NULL),
  (7, 'BOX', 'Carton', 'Ege', 5, 30, 'WH-02', NULL, NULL, 'rejected', 'no_range', '2026-09-03T08:00:00.000Z', '2026-09-03T09:00:00.000Z', 'too dear', 'Orhan · D-2', 'Ada · A-1');
INSERT INTO purchase_orders (id, product_barcode, product_name_at_order, supplier, quantity, unit_price, site, status, hold_reason)
  VALUES (9, 'BOX', 'Carton', 'Ege', 1, 1, 'WH-02', 'pending', 'no_range');
DELETE FROM purchase_orders WHERE id = 9;
PRAGMA user_version = 3;
"""


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _old_db(tmp_path, script=_V3_DB):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(script)
    old.close()


def _cols(conn, table):
    return {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}


def _orders(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT id, product_barcode, supplier, quantity, unit_price, site, range_min, range_max, status, hold_reason, "
        "created_at, decided_at, decision_note, raised_by, decided_by FROM purchase_orders ORDER BY id")]


def test_a_v3_database_is_rebuilt_with_data_and_ids_intact(tmp_path):
    _old_db(tmp_path)
    old = sqlite3.connect(tmp_path / "t.db")
    before = _orders(old)
    old.close()

    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION >= 4
        assert _orders(conn) == before  # every value and id, including the gap at 3-6
        cols = _cols(conn, "purchase_orders")
        for name in ("received_qty", "received_at", "received_by", "cancelled_at", "cancelled_by"):
            assert name in cols
        assert [r[0] for r in conn.execute("SELECT received_qty FROM purchase_orders")] == [0, 0, 0]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'purchase_orders_v4'").fetchone() is None
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(purchase_orders)")}
        assert {"idx_purchase_orders_status", "idx_purchase_orders_created_at"} <= indexes
        # the ids of deleted rows are never handed out again (9 was the highest ever used)
        new_id = po_repo.submit("BOX", "Ege", 1, 5, "WH-02").id
        assert new_id == 10

    assert [o.status for o in po_repo.list_orders()][-3:] == ["rejected", "sent", "pending"]  # oldest last


def test_the_rebuilt_table_accepts_the_new_states_and_rejects_nonsense(tmp_path):
    _old_db(tmp_path)
    with connection.connection_scope() as conn:
        for status in ("received", "partially_received", "cancelled"):
            conn.execute("UPDATE purchase_orders SET status = ? WHERE id = 2", (status,))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE purchase_orders SET status = 'lost' WHERE id = 2")
        with pytest.raises(sqlite3.IntegrityError):  # cannot receive more than ordered
            conn.execute("UPDATE purchase_orders SET received_qty = 11 WHERE id = 2")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE purchase_orders SET received_qty = -1 WHERE id = 2")


def test_an_old_database_can_then_receive_against_its_orders(tmp_path):
    _old_db(tmp_path)
    from database import warehouse_repository
    from shared.models import StockLocation, Warehouse
    from shared.auth import Actor

    warehouse_repository.create(Warehouse(code="WH-01", name="Merkez"))
    done = po_repo.receive_against_order(2, 4, Actor("D-1", "Deniz"), StockLocation.warehouse("WH-01"))
    assert (done.status, done.received_qty) == ("partially_received", 4)
    assert product_repository.get_by_barcode("PLT").stock_quantity == 9
    assert product_repository.get_by_barcode("PLT").cost_price == 600.0  # cost was unknown: the price paid


def test_cost_columns_default_to_unknown_on_existing_rows(tmp_path):
    _old_db(tmp_path)
    with connection.connection_scope() as conn:
        assert [tuple(r) for r in conn.execute("SELECT barcode, cost_price FROM products ORDER BY barcode")] == [
            ("BOX", 0.0), ("PLT", 0.0)]
        assert [tuple(r) for r in conn.execute("SELECT unit_cost_at_sale, cost_known FROM transaction_items")] == [(0.0, 0)]
        assert "cost_price" in _cols(conn, "products")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE products SET cost_price = -1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE transaction_items SET cost_known = 2")
    assert product_repository.get_by_barcode("PLT").cost_price == 0.0
    assert not product_repository.get_by_barcode("PLT").cost_known


def test_migration_is_idempotent_and_skips_the_rebuild_when_current(tmp_path, monkeypatch):
    _old_db(tmp_path)
    with connection.connection_scope() as conn:
        first = _orders(conn)
        conn.execute("PRAGMA user_version = 3")  # pretend it never ran, on an already-migrated schema
    rebuilds = []
    real = migrations._rebuild_purchase_orders
    monkeypatch.setattr(migrations, "_rebuild_purchase_orders", lambda c: rebuilds.append(1) or real(c))
    connection._initialized = False
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert _orders(conn) == first
    assert rebuilds == []  # already the new shape: nothing rebuilt


def test_a_fresh_database_already_has_the_new_shape():
    with connection.connection_scope() as conn:
        assert migrations._purchase_orders_have_v4_shape(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert {"cost_price"} <= set(_cols(conn, "products"))
        assert {"unit_cost_at_sale", "cost_known"} <= set(_cols(conn, "transaction_items"))


def test_a_pre_sign_in_purchase_orders_table_is_upgraded_through_v2_then_rebuilt(tmp_path):
    base = _V3_DB.split("INSERT INTO purchase_orders")[0].replace(", raised_by TEXT, decided_by TEXT", "")
    _old_db(tmp_path, base + """
INSERT INTO purchase_orders (id, product_barcode, product_name_at_order, supplier, quantity, unit_price, site, status, hold_reason)
  VALUES (3, 'PLT', 'Pallet wrap', 'Kuzey', 40, 742.5, 'WH-01', 'pending', 'above_range');
PRAGMA user_version = 1;
""")
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
        assert {"raised_by", "decided_by", "received_qty", "cancelled_by"} <= set(_cols(conn, "purchase_orders"))
        assert [tuple(r) for r in conn.execute("SELECT id, status, received_qty, raised_by FROM purchase_orders")] == [
            (3, "pending", 0, None)]


def test_a_failure_in_the_rebuild_leaves_the_old_database_untouched(tmp_path, monkeypatch):
    _old_db(tmp_path)
    monkeypatch.setattr(migrations, "_PURCHASE_ORDERS_V4", "CREATE TABLE purchase_orders_v4 (id INTEGER, oops")
    raw = sqlite3.connect(tmp_path / "t.db", isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    with pytest.raises(sqlite3.Error):
        migrations.run_migrations(raw)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "cost_price" not in _cols(raw, "products")  # the column step rolled back with it
    assert len(_orders(raw)) == 3 and "received_qty" not in _cols(raw, "purchase_orders")
    raw.close()


def test_a_later_version_block_runs_after_v4_without_disturbing_it(tmp_path, monkeypatch):
    """Another engineer adds a v5 step: v4 must still run first, exactly once."""
    _old_db(tmp_path)
    ran = []

    def to_v5(conn):
        # by now v4 has happened: the new status is legal and the cost column exists
        assert migrations._purchase_orders_have_v4_shape(conn) and "cost_price" in _cols(conn, "products")
        ran.append(5)
        conn.execute("CREATE TABLE IF NOT EXISTS v5_marker (id INTEGER)")

    monkeypatch.setitem(migrations._STEPS, 5, to_v5)
    monkeypatch.setattr(migrations, "LATEST_VERSION", 5)
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert ran == [5] and len(_orders(conn)) == 3
    connection._initialized = False
    with connection.connection_scope():
        pass
    assert ran == [5]  # not re-run
