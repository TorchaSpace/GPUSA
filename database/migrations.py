"""Bringing an existing shared_backend.db up to the current schema.

schema.sql is written with `CREATE TABLE IF NOT EXISTS`, which is enough
for NEW tables but does nothing to a table that already exists - so a
database created by an earlier build never gets columns added to it
later. This module is the other half: after schema.sql has run,
run_migrations() adds whatever columns an older database is missing and
performs the one-time data steps that go with them.

Versioning uses SQLite's own `PRAGMA user_version` (0 on any database
this code has never touched). Each step runs once, inside one
BEGIN IMMEDIATE transaction together with the version bump, and
re-checks the version after taking the write lock - so when POS, Depot
and Admin all start at the same moment against an old database, exactly
one of them migrates and the others see the new version and do nothing.

Version 2 - sign-in: stock_movements.handled_by, transactions.cashier,
purchase_orders.raised_by / decided_by ("name · badge" snapshots; NULL on
older rows). The accounts / auth_events tables are new, so schema.sql
creates them.

Version 1 - per-location stock (the Warehouse slice):
- stock_movements gains location_kind / location_code / reason /
  reference, transactions gains dealership_code, shipments gains
  origin_code / stock_moved (all already in schema.sql for new
  databases; added here only where missing).
- Shipments already on the road (in_transit) are marked stock_moved = 1:
  their goods are on the truck, not in any location.
- Every product's existing company-wide stock_quantity, minus what's on
  those trucks, becomes an 'unassigned' stock level - it has to live
  somewhere, and nothing recorded where it physically is. Admin >
  Warehouses shows it and moves it to a warehouse or dealership.
"""

from __future__ import annotations

import sqlite3

LATEST_VERSION = 2

# (table, column, declaration) - declarations match schema.sql exactly.
_V1_COLUMNS = (
    ("stock_movements", "location_kind", "TEXT"),
    ("stock_movements", "location_code", "TEXT"),
    ("stock_movements", "reason", "TEXT"),
    ("stock_movements", "reference", "TEXT"),
    ("transactions", "dealership_code", "TEXT"),
    ("shipments", "origin_code", "TEXT"),
    ("shipments", "stock_moved", "INTEGER NOT NULL DEFAULT 0 CHECK (stock_moved IN (0, 1))"),
)

# Indexes on the columns above live here rather than in schema.sql: on an
# old database schema.sql runs BEFORE the columns exist, and a CREATE
# INDEX on a missing column would fail the whole script.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_stock_movements_location ON stock_movements(location_kind, location_code)",
    "CREATE INDEX IF NOT EXISTS idx_shipments_origin_code ON shipments(origin_code)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_dealership_code ON transactions(dealership_code)",
)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _to_v1(conn: sqlite3.Connection) -> None:
    existing: dict[str, set[str]] = {}
    for table, column, declaration in _V1_COLUMNS:
        cols = existing.setdefault(table, _columns(conn, table))
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            cols.add(column)

    conn.execute("UPDATE shipments SET stock_moved = 1 WHERE status = 'in_transit'")

    in_transit = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT l.product_barcode, SUM(l.expected_qty) FROM shipment_lines l "
            "JOIN shipments s ON s.id = l.shipment_id "
            "WHERE s.status = 'in_transit' AND s.stock_moved = 1 GROUP BY l.product_barcode"
        ).fetchall()
    }
    placed = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT product_barcode, SUM(quantity) FROM stock_levels GROUP BY product_barcode"
        ).fetchall()
    }
    for barcode, total in conn.execute("SELECT barcode, stock_quantity FROM products").fetchall():
        transit = in_transit.get(barcode, 0)
        already = placed.get(barcode, 0)
        unassigned = max(0, int(total) - already - transit)
        if unassigned:
            conn.execute(
                "INSERT INTO stock_levels (location_kind, location_code, product_barcode, quantity) "
                "VALUES ('unassigned', '', ?, ?) "
                "ON CONFLICT (location_kind, location_code, product_barcode) "
                "DO UPDATE SET quantity = quantity + excluded.quantity",
                (barcode, unassigned),
            )
        # Only differs when more was on trucks than the old total said
        # existed; the total is re-derived so the invariant holds from here.
        consistent = already + unassigned + transit
        if consistent != total:
            conn.execute("UPDATE products SET stock_quantity = ? WHERE barcode = ?", (consistent, barcode))


# Version 2 - sign-in (the auth slice): who did it, as text snapshots.
_V2_COLUMNS = (
    ("stock_movements", "handled_by", "TEXT"),
    ("transactions", "cashier", "TEXT"),
    ("purchase_orders", "raised_by", "TEXT"),
    ("purchase_orders", "decided_by", "TEXT"),
)


def _add_columns(conn: sqlite3.Connection, columns) -> None:
    existing: dict[str, set[str]] = {}
    for table, column, declaration in columns:
        cols = existing.setdefault(table, _columns(conn, table))
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            cols.add(column)


def _to_v2(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V2_COLUMNS)


_STEPS = {1: _to_v1, 2: _to_v2}


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply every step above the database's current version. Safe to call
    on every startup; a no-op once the database is current."""
    if _version(conn) < LATEST_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = _version(conn)  # re-read under the write lock
            for step in range(version + 1, LATEST_VERSION + 1):
                _STEPS[step](conn)
            if version < LATEST_VERSION:
                conn.execute(f"PRAGMA user_version = {LATEST_VERSION}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    for statement in _INDEXES:
        conn.execute(statement)
