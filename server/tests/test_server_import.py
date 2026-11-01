"""Copying a desktop SQLite file into PostgreSQL."""

import os
import pathlib
import sqlite3

import pytest

pytest.importorskip("alembic")

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from gpusa_server import import_sqlite  # noqa: E402
from gpusa_server.config import Settings  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test the import")

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_SQL = SERVER_DIR.parent / "database" / "schema.sql"


@pytest.fixture(scope="module", autouse=True)
def _schema():
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", URL)
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(cfg, "head")
    yield
    mp.undo()


@pytest.fixture()
def settings():
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("TRUNCATE " + ", ".join(import_sqlite.TABLES) + " RESTART IDENTITY CASCADE")
    return Settings(database_url=URL)


@pytest.fixture()
def desktop_db(tmp_path):
    path = tmp_path / "shared_backend.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    db.executescript("""
        INSERT INTO products (barcode, name, price, stock_quantity, cost_price) VALUES ('A1', 'Anvil', 9.5, 10, 4.25);
        INSERT INTO products (barcode, name, price, stock_quantity, is_active) VALUES ('B2', 'Old', 1, 0, 0);
        INSERT INTO warehouses (code, name) VALUES ('WH-01', 'Main');
        INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) VALUES ('warehouse', 'WH-01', 'A1', 10);
        INSERT INTO transactions (total, payment_method, client_uuid) VALUES (19, 'cash', 'u-1');
        INSERT INTO transaction_items (transaction_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity, cost_known)
            VALUES (1, 'A1', 'Anvil', 9.5, 2, 1);
        INSERT INTO employees (badge_id, name, role, location_type, location_name) VALUES ('B-7', 'Ayse', 'Management', 'Warehouse', 'HQ');
        INSERT INTO accounts (employee_id, role, pin_hash, locked_until) VALUES (1, 'admin', 'pbkdf2_sha256$1$aa$bb', '');
        INSERT INTO activity_events (kind, data_json) VALUES ('x', '{"a": 1}');
        INSERT INTO ledger_entries (direction, doc_type, doc_no, counterparty, issue_date, due_date, amount)
            VALUES ('in', 'check', 'C1', 'Bob', '2026-10-01', '2026-11-01', 100);
    """)
    db.commit()
    db.close()
    return path


def pg_one(sql_text):
    with psycopg.connect(URL) as conn:
        return conn.execute(sql_text).fetchone()[0]


def test_dry_run_writes_nothing(settings, desktop_db):
    result = import_sqlite.import_file(settings, desktop_db)
    assert result["counts"]["products"] == 2 and not result["applied"]
    assert pg_one("SELECT count(*) FROM products") == 0


def test_apply_copies_rows_types_and_keeps_ids_usable(settings, desktop_db):
    result = import_sqlite.import_file(settings, desktop_db, apply=True)
    assert result["stock_problems"] == []
    assert pg_one("SELECT count(*) FROM transaction_items") == 1
    assert pg_one("SELECT is_active FROM products WHERE barcode = 'B2'") is False
    assert float(pg_one("SELECT cost_price FROM products WHERE barcode = 'A1'")) == 4.25
    assert pg_one("SELECT locked_until IS NULL FROM accounts") is True  # '' became NULL
    assert pg_one("SELECT data_json->>'a' FROM activity_events") == "1"
    assert str(pg_one("SELECT due_date FROM ledger_entries")) == "2026-11-01"
    # The next row gets a fresh id, not 1 again.
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("INSERT INTO transactions (total) VALUES (1)")
    assert pg_one("SELECT max(id) FROM transactions") == 2


def test_refuses_a_database_that_already_has_data_unless_wiped(settings, desktop_db):
    import_sqlite.import_file(settings, desktop_db, apply=True)
    with pytest.raises(import_sqlite.ImportProblem):
        import_sqlite.import_file(settings, desktop_db, apply=True)
    import_sqlite.import_file(settings, desktop_db, apply=True, wipe=True)
    assert pg_one("SELECT count(*) FROM products") == 2


def test_stock_mismatch_is_reported(settings, desktop_db):
    db = sqlite3.connect(desktop_db)
    db.execute("UPDATE products SET stock_quantity = 99 WHERE barcode = 'A1'")
    db.commit()
    db.close()
    result = import_sqlite.import_file(settings, desktop_db)
    assert len(result["stock_problems"]) == 1 and "A1" in result["stock_problems"][0]


def test_older_file_with_missing_columns_and_tables_imports(settings, tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE products (barcode TEXT PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL,
                               stock_quantity INTEGER NOT NULL DEFAULT 0);
        INSERT INTO products VALUES ('A1', 'Anvil', 2, 0);
    """)
    db.commit()
    db.close()
    result = import_sqlite.import_file(settings, path, apply=True)
    assert result["counts"]["products"] == 1 and result["counts"]["accounts"] == 0


def test_a_broken_file_is_a_clear_error(settings, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a database" * 100)
    with pytest.raises(import_sqlite.ImportProblem):
        import_sqlite.import_file(settings, bad)
