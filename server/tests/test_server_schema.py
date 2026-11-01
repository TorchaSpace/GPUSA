"""The Alembic migrations build the same tables the desktop SQLite has.

Runs only against a real PostgreSQL (GPUSA_TEST_DATABASE_URL); it wipes and
rebuilds the schema of that database, so point it at a throwaway one.
"""

import os
import pathlib
import sqlite3

import pytest

pytest.importorskip("alembic")
pytest.importorskip("sqlalchemy")

import psycopg  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test the schema")

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_SQL = SERVER_DIR.parent / "database" / "schema.sql"


@pytest.fixture(scope="module")
def pg():
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", URL)
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with psycopg.connect(URL, autocommit=True) as conn:
        yield conn, cfg
    mp.undo()


def _pg_columns(conn):
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
    ).fetchall()
    out = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    return out


def _sqlite_columns():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: {r[1] for r in db.execute(f"PRAGMA table_info({t})")} for t in tables}


def test_same_tables_and_columns_as_sqlite(pg):
    conn, _ = pg
    assert _pg_columns(conn) == _sqlite_columns()


def test_barcode_is_unique_ignoring_case(pg):
    conn, _ = pg
    conn.execute("INSERT INTO products (barcode, name, price) VALUES ('ABC1', 'x', 1.50)")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO products (barcode, name, price) VALUES ('abc1', 'y', 2)")


def test_sale_is_stored_once_per_client_uuid(pg):
    conn, _ = pg
    conn.execute("INSERT INTO transactions (total, client_uuid) VALUES (5, 'u-1')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO transactions (total, client_uuid) VALUES (5, 'u-1')")
    # Old sales have no key, and any number of them is fine.
    conn.execute("INSERT INTO transactions (total) VALUES (1)")
    conn.execute("INSERT INTO transactions (total) VALUES (1)")


def test_checks_are_enforced(pg):
    conn, _ = pg
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO products (barcode, name, price) VALUES ('NEG', 'n', -1)")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO transactions (total, payment_method) VALUES (1, 'bitcoin')")


def test_audit_tables_are_append_only(pg):
    conn, _ = pg
    conn.execute("INSERT INTO admin_audit (action, kind, target) VALUES ('delete', 'product', 'P1')")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        conn.execute("UPDATE admin_audit SET target = 'x'")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        conn.execute("DELETE FROM admin_audit")
    conn.execute(
        "INSERT INTO ledger_audit (entry_id, actor_badge, actor_name, action, after_json) "
        "VALUES (1, 'B-1', 'A', 'created', '{\"a\": 1}')")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
        conn.execute("DELETE FROM ledger_audit")


def test_downgrade_and_upgrade_roundtrip(pg):
    conn, cfg = pg
    command.downgrade(cfg, "base")
    assert _pg_columns(conn) == {}
    command.upgrade(cfg, "head")
    assert set(_pg_columns(conn)) == set(_sqlite_columns())
