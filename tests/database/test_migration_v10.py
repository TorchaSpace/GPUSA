"""Migration v10: sale_returns accepts total 0 (refund of a free item) without losing refunds, items or ids."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import migrations


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _v9_database(path):
    """Build a current database, then put sale_returns back to its v9 shape (CHECK total > 0)."""
    with connection.connection_scope() as conn:
        conn.execute("INSERT INTO products (barcode, name, price, stock_quantity) VALUES ('X', 'X', 1, 5)")
        conn.execute("INSERT INTO transactions (total) VALUES (3)")
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA foreign_keys = OFF")
    raw.executescript("""
        DROP TABLE sale_return_items; DROP TABLE sale_returns;
        CREATE TABLE sale_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            dealership_code TEXT,
            total REAL NOT NULL CHECK (total > 0),
            payment_method TEXT CHECK (payment_method IS NULL OR payment_method IN ('card', 'cash')),
            reason TEXT NOT NULL, requested_by TEXT, approved_by TEXT, client_uuid TEXT);
        CREATE TABLE sale_return_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            return_id INTEGER NOT NULL REFERENCES sale_returns(id) ON DELETE CASCADE,
            product_barcode TEXT NOT NULL REFERENCES products(barcode),
            product_name_at_sale TEXT NOT NULL, unit_price_at_sale REAL NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            restock INTEGER NOT NULL DEFAULT 1 CHECK (restock IN (0, 1)));
        DROP INDEX IF EXISTS idx_sale_returns_transaction_id;
        DROP INDEX IF EXISTS idx_sale_returns_created_at;
        DROP INDEX IF EXISTS idx_sale_returns_client_uuid;
        INSERT INTO sale_returns (id, transaction_id, total, reason, client_uuid) VALUES (1, 1, 2, 'a', 'u1');
        INSERT INTO sale_returns (id, transaction_id, total, reason, client_uuid) VALUES (7, 1, 1, 'b', NULL);
        INSERT INTO sale_return_items (return_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity)
            VALUES (1, 'X', 'X', 1, 2), (7, 'X', 'X', 1, 1);
        DELETE FROM sale_return_items WHERE return_id = 7; DELETE FROM sale_returns WHERE id = 7;  -- id 7 stays burnt in sqlite_sequence
        INSERT INTO sale_returns (id, transaction_id, total, reason) VALUES (3, 1, 1, 'c');
        INSERT INTO sale_return_items (return_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity)
            VALUES (3, 'X', 'X', 1, 1);
        PRAGMA user_version = 9;
    """)
    raw.commit()
    raw.close()


def test_a_v9_database_keeps_its_refunds_items_and_ids_and_accepts_a_free_refund(tmp_path):
    _v9_database(tmp_path / "t.db")
    connection._initialized = False
    with connection.connection_scope() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION >= 10
        assert [r["id"] for r in conn.execute("SELECT id FROM sale_returns ORDER BY id")] == [1, 3]
        assert conn.execute("SELECT COUNT(*) FROM sale_return_items").fetchone()[0] == 2  # not cascade-deleted
        assert conn.execute("SELECT client_uuid FROM sale_returns WHERE id = 1").fetchone()[0] == "u1"
        assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'sale_returns'").fetchone()[0] >= 7
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_sale_returns_transaction_id", "idx_sale_returns_created_at",
                "idx_sale_returns_client_uuid"} <= names
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on
        new_id = conn.execute("INSERT INTO sale_returns (transaction_id, total, reason) VALUES (1, 0, 'free')").lastrowid
        assert new_id > 7  # a burnt id is never handed out again
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO sale_returns (transaction_id, total, reason) VALUES (1, -1, 'bad')")
        with pytest.raises(sqlite3.IntegrityError):  # the unique key still holds
            conn.execute("INSERT INTO sale_returns (transaction_id, total, reason, client_uuid) VALUES (1, 1, 'd', 'u1')")


def test_the_step_is_a_no_op_on_a_current_database(tmp_path):
    with connection.connection_scope() as conn:
        before = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sale_returns'").fetchone()[0]
        conn.execute("BEGIN IMMEDIATE")
        migrations._to_v10(conn)
        conn.execute("COMMIT")
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name = 'sale_returns'").fetchone()[0] == before
        assert "total >= 0" in before
