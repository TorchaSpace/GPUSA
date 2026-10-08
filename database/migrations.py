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

Version 5 - ledger audit trail: ledger_entries.created_by / settled_by /
updated_by ("name · badge" snapshots; NULL on rows from before auditing) and
the append-only ledger_audit table (who / when / what, one row per change,
written in the same transaction as the change; entry_id is a plain integer,
never a foreign key, so deleting an entry can't delete its history). The
table is new, so schema.sql creates it; the step re-creates it (and its
no-update / no-delete triggers) only if missing.

Version 4 - purchase-order receiving, product cost and profit:
- purchase_orders is REBUILT (create new, copy, drop, rename - all inside
  the migration's one transaction, ids and values kept, AUTOINCREMENT
  counter preserved) because SQLite can't alter a CHECK constraint: status
  now also allows received / partially_received / cancelled, and the table
  gains received_qty (default 0), received_at, received_by, cancelled_at,
  cancelled_by. Nothing refers to purchase_orders, so no foreign key moves.
  Skipped when the table already has the new shape (a new database).
- products.cost_price (default 0 = unknown), transaction_items.unit_cost_at_sale
  (default 0) and transaction_items.cost_known (default 0: every sale made
  before costing existed stays "cost unknown" and is left out of profit).

Version 3 - product safety: products.is_active (default 1: every existing
product stays active) and a case-insensitive UNIQUE index on
products.barcode (skipped, harmlessly, if an old database already holds two
barcodes that differ only by case - product_repository then enforces it in
code). Existing barcodes are NOT rewritten to upper-case: other tables point
at them.

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

LATEST_VERSION = 8

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


_V3_COLUMNS = (
    ("products", "is_active", "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))"),
)


def _ensure_barcode_nocase_index(conn: sqlite3.Connection) -> None:
    """Case-insensitive uniqueness for barcodes. Tolerant: if the table
    already has "abc"/"ABC" the index can't exist - leave it (the repository
    checks in code) rather than refuse to start."""
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode_nocase ON products(barcode COLLATE NOCASE)"
        )
    except sqlite3.IntegrityError:
        pass


def _to_v3(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V3_COLUMNS)
    _ensure_barcode_nocase_index(conn)


# Version 4 - receiving, cost, profit. The purchase_orders definition below
# is schema.sql's, spelled out because the rebuild has to create it.
_V4_COLUMNS = (
    ("products", "cost_price", "REAL NOT NULL DEFAULT 0 CHECK (cost_price >= 0)"),
    ("transaction_items", "unit_cost_at_sale", "REAL NOT NULL DEFAULT 0 CHECK (unit_cost_at_sale >= 0)"),
    ("transaction_items", "cost_known", "INTEGER NOT NULL DEFAULT 0 CHECK (cost_known IN (0, 1))"),
)

_PURCHASE_ORDERS_V4 = """
CREATE TABLE purchase_orders_v4 (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    product_barcode       TEXT NOT NULL REFERENCES products(barcode),
    product_name_at_order TEXT NOT NULL,
    supplier              TEXT NOT NULL,
    quantity              INTEGER NOT NULL CHECK (quantity > 0),
    unit_price            REAL NOT NULL CHECK (unit_price > 0),
    site                  TEXT NOT NULL,
    range_min             REAL,
    range_max             REAL,
    status                TEXT NOT NULL CHECK (status IN
                          ('pending', 'sent', 'rejected', 'received', 'partially_received', 'cancelled')),
    hold_reason           TEXT CHECK (hold_reason IN ('above_range', 'below_range', 'no_range')),
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    decided_at            TEXT,
    decision_note         TEXT,
    raised_by             TEXT,
    decided_by            TEXT,
    received_qty          INTEGER NOT NULL DEFAULT 0 CHECK (received_qty >= 0 AND received_qty <= quantity),
    received_at           TEXT,
    received_by           TEXT,
    cancelled_at          TEXT,
    cancelled_by          TEXT
)
"""

_PURCHASE_ORDER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_purchase_orders_status ON purchase_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_purchase_orders_created_at ON purchase_orders(created_at)",
)


def _purchase_orders_have_v4_shape(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'purchase_orders'").fetchone()
    return row is not None and "partially_received" in (row[0] or "") and "received_qty" in (row[0] or "")


def _rebuild_purchase_orders(conn: sqlite3.Connection) -> None:
    """Swap purchase_orders for a copy with the v4 constraint and columns.
    Runs inside the caller's transaction, so a failure leaves the old table
    untouched."""
    old_columns = [row[1] for row in conn.execute("PRAGMA table_info(purchase_orders)")]
    seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'purchase_orders'").fetchone()
    conn.execute("DROP TABLE IF EXISTS purchase_orders_v4")
    conn.execute(_PURCHASE_ORDERS_V4)
    new_columns = {row[1] for row in conn.execute("PRAGMA table_info(purchase_orders_v4)")}
    shared = [c for c in old_columns if c in new_columns]
    names = ", ".join(shared)
    conn.execute(f"INSERT INTO purchase_orders_v4 ({names}) SELECT {names} FROM purchase_orders")
    conn.execute("DROP TABLE purchase_orders")
    conn.execute("ALTER TABLE purchase_orders_v4 RENAME TO purchase_orders")
    if seq_row is not None:  # ids of deleted/rejected rows must never be handed out again
        conn.execute(
            "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'purchase_orders'", (int(seq_row[0]),)
        )
    for statement in _PURCHASE_ORDER_INDEXES:
        conn.execute(statement)


def _to_v4(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V4_COLUMNS)
    if not _purchase_orders_have_v4_shape(conn):
        _rebuild_purchase_orders(conn)


# Version 5 - ledger audit trail.
_V5_COLUMNS = (
    ("ledger_entries", "created_by", "TEXT"),
    ("ledger_entries", "settled_by", "TEXT"),
    ("ledger_entries", "updated_by", "TEXT"),
)

_LEDGER_AUDIT_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS ledger_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor_badge TEXT NOT NULL,
    actor_name  TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('created', 'edited', 'cleared', 'endorsed', 'reopened', 'deleted')),
    before_json TEXT,
    after_json  TEXT
)""",
    "CREATE INDEX IF NOT EXISTS idx_ledger_audit_entry ON ledger_audit(entry_id, id)",
    """
CREATE TRIGGER IF NOT EXISTS trg_ledger_audit_no_update BEFORE UPDATE ON ledger_audit
BEGIN SELECT RAISE(ABORT, 'ledger_audit is append-only'); END""",
    """
CREATE TRIGGER IF NOT EXISTS trg_ledger_audit_no_delete BEFORE DELETE ON ledger_audit
BEGIN SELECT RAISE(ABORT, 'ledger_audit is append-only'); END""",
)


def _to_v5(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V5_COLUMNS)
    for statement in _LEDGER_AUDIT_STATEMENTS:
        conn.execute(statement)


# Version 6 - the Floor's bin / dock door gets its own column (it used to be glued onto `note`).
_V6_COLUMNS = (("stock_movements", "bin_code", "TEXT"),)


def _to_v6(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V6_COLUMNS)


# Version 7 - the till records how each sale was paid (Card / Cash used to finalize identically).
_V7_COLUMNS = (
    ("transactions", "payment_method",
     "TEXT CHECK (payment_method IS NULL OR payment_method IN ('card', 'cash'))"),
)


def _to_v7(conn: sqlite3.Connection) -> None:
    _add_columns(conn, _V7_COLUMNS)


def _to_v8(conn: sqlite3.Connection) -> None:
    """Version 8 - dealership stock requests. The table and its indexes come
    from schema.sql (CREATE ... IF NOT EXISTS runs before this on every
    startup), so an older database already has them; nothing to backfill."""


_STEPS = {1: _to_v1, 2: _to_v2, 3: _to_v3, 4: _to_v4, 5: _to_v5, 6: _to_v6, 7: _to_v7, 8: _to_v8}


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
    _ensure_barcode_nocase_index(conn)
