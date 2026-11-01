"""One-time copy of a desktop ``shared_backend.db`` (SQLite) into PostgreSQL.

    python -m gpusa_server.import_sqlite /path/to/shared_backend.db            # dry run: reads, checks, rolls back
    python -m gpusa_server.import_sqlite /path/to/shared_backend.db --apply    # really writes

* The SQLite file is opened read-only and never changed. Work on a copy.
* Only columns that exist on both sides are copied, so a database from an
  older desktop version (fewer columns) imports fine; tables it lacks are skipped.
* Everything happens in ONE transaction: any error leaves PostgreSQL untouched.
* The target tables must be empty (the first administrator made by
  ``create_admin`` counts). ``--wipe`` empties them first; the accounts then
  come from the desktop file, so sign in with the desktop administrator's PIN.
* After copying, the stock rule the desktop enforces is re-checked:
  ``products.stock_quantity = SUM(stock_levels) + units on the truck``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from psycopg import sql

from . import db
from .config import load_settings

# Parents before children (foreign keys). Same order as migration 0001.
TABLES = [
    "products", "transactions", "transaction_items", "stock_movements",
    "dealerships", "employees", "attendance_records", "purchase_price_ranges",
    "purchase_orders", "ledger_entries", "ledger_audit", "shipments",
    "shipment_lines", "warehouses", "stock_levels", "location_reorder_levels",
    "accounts", "auth_events", "app_settings", "stock_requests", "admin_audit",
    "sale_returns", "sale_return_items", "day_closes", "activity_events",
]


class ImportProblem(RuntimeError):
    """The file cannot be imported as it is (message says why)."""


def _sqlite_columns(src: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]


def _pg_columns(conn, table: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s", (table,)).fetchall()
    return {name: kind for name, kind in rows}


def _convert(value, pg_type: str):
    if pg_type == "boolean" and value is not None:
        return bool(value)
    if value == "" and pg_type != "text":
        return None  # an empty timestamp/date/number means "not set"
    return value


def _copy_table(src, conn, table: str) -> int:
    have = _sqlite_columns(src, table)
    if not have:
        return 0  # table missing in an older file
    pg = _pg_columns(conn, table)
    columns = [c for c in have if c in pg]
    if not columns:
        return 0
    rows = src.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in columns)} FROM "{table}"').fetchall()
    if not rows:
        return 0
    query = sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with conn.cursor() as cur:
        cur.executemany(query, [[_convert(v, pg[c]) for v, c in zip(row, columns)] for row in rows])
    return len(rows)


def _reset_sequences(conn) -> None:
    for table in TABLES:
        if "id" not in _pg_columns(conn, table):
            continue
        conn.execute(
            sql.SQL("SELECT setval(pg_get_serial_sequence({t}, 'id'), COALESCE((SELECT max(id) FROM {tbl}), 0) + 1, false)")
            .format(t=sql.Literal(table), tbl=sql.Identifier(table)))


def stock_problems(conn) -> list[str]:
    """Products whose total differs from the sum over places + goods on the truck."""
    rows = conn.execute(
        """
        SELECT p.barcode, p.stock_quantity,
               COALESCE((SELECT SUM(quantity) FROM stock_levels s WHERE s.product_barcode = p.barcode), 0)
             + COALESCE((SELECT SUM(l.expected_qty) FROM shipment_lines l JOIN shipments sh ON sh.id = l.shipment_id
                         WHERE l.product_barcode = p.barcode AND sh.status = 'in_transit' AND sh.stock_moved), 0)
        FROM products p
        """).fetchall()
    return [f"{b}: total {t}, places + truck {s}" for b, t, s in rows if t != s]


def import_file(settings, sqlite_path: Path, *, apply: bool = False, wipe: bool = False) -> dict:
    """Copy everything. Returns {'counts': {...}, 'stock_problems': [...], 'applied': bool}."""
    if not sqlite_path.is_file():
        raise ImportProblem(f"File not found: {sqlite_path}")
    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        counts: dict[str, int] = {}
        with db.connect(settings) as conn:
            existing = {t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
                        for t in TABLES}
            busy = [t for t, n in existing.items() if n]
            if busy and not wipe:
                raise ImportProblem("PostgreSQL already has data in: " + ", ".join(busy)
                                    + ". Use --wipe to empty it first.")
            if busy:
                conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(
                    sql.SQL(", ").join(sql.Identifier(t) for t in TABLES)))
            for table in TABLES:
                counts[table] = _copy_table(src, conn, table)
            _reset_sequences(conn)
            problems = stock_problems(conn)
            if not apply:
                conn.rollback()
        return {"counts": counts, "stock_problems": problems, "applied": apply}
    except sqlite3.DatabaseError as exc:
        raise ImportProblem(f"Not a readable SQLite file: {exc}") from exc
    finally:
        src.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_file", type=Path)
    parser.add_argument("--apply", action="store_true", help="really write (default: dry run)")
    parser.add_argument("--wipe", action="store_true", help="empty the PostgreSQL tables first")
    args = parser.parse_args(argv)
    try:
        result = import_file(load_settings(), args.sqlite_file, apply=args.apply, wipe=args.wipe)
    except ImportProblem as exc:
        print(f"Cannot import: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # constraint errors etc.: nothing was written
        print(f"Import failed, nothing was written: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for table, n in result["counts"].items():
        if n:
            print(f"  {table:<24} {n}")
    print(f"Total rows: {sum(result['counts'].values())}")
    for line in result["stock_problems"]:
        print(f"  STOCK MISMATCH {line}")
    if result["stock_problems"]:
        print(f"{len(result['stock_problems'])} product(s) fail the stock rule (same as in the desktop file?).")
    print("Written." if result["applied"] else "Dry run only: nothing written. Add --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
