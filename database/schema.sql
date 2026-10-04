-- Schema for the shared POS / Inventory backend (shared_backend.db).
--
-- This file is the single source of truth for table structure. All
-- three desktop apps (pos_app, depot_app, admin_app) read/write through
-- database/ repositories only - nothing outside this package should
-- ever run SQL against these tables directly.
--
-- WAL mode (set in database/connection.py, not here) lets one writer and
-- multiple readers work against this file concurrently, which is what
-- lets all three apps share it safely as separate processes.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS products (
    barcode             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    price               REAL NOT NULL CHECK (price >= 0),
    stock_quantity      INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    critical_stock_level INTEGER NOT NULL DEFAULT 0 CHECK (critical_stock_level >= 0),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- 0 = deactivated (migration v3): kept for history and shown dimmed in
    -- Admin, but not sellable / receivable / shippable. A product with stock
    -- or history can't be deleted, only deactivated.
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- Weighted-average unit cost (migration v4); 0 = not known yet. Moved by
    -- purchase-order receipts (database/purchase_order_repository.py) or
    -- typed in Admin's product form.
    cost_price          REAL NOT NULL DEFAULT 0 CHECK (cost_price >= 0)
);
-- Barcodes are stored trimmed + upper-case by product_repository; the
-- case-insensitive UNIQUE index on them is created by database/migrations.py
-- (v3), which skips it if an old database already holds case-only duplicates.

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    total       REAL NOT NULL CHECK (total >= 0),
    -- The dealership whose shelf the goods came off (NULL: a terminal
    -- with no dealership identity, selling from unassigned stock, or a
    -- sale made before per-location stock existed).
    dealership_code TEXT,
    -- The signed-in cashier ("name · badge" snapshot; migration v2).
    cashier         TEXT
);

-- Line items are stored separately from `transactions` (rather than as a
-- serialized blob) so sales reports can query/aggregate per-product sales
-- with plain SQL. `unit_price_at_sale` is captured at checkout time so a
-- later price change never rewrites the history of a past sale.
CREATE TABLE IF NOT EXISTS transaction_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    product_barcode     TEXT NOT NULL REFERENCES products(barcode),
    product_name_at_sale TEXT NOT NULL,
    unit_price_at_sale  REAL NOT NULL CHECK (unit_price_at_sale >= 0),
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    -- The product's cost per unit when it was sold (migration v4), so profit
    -- is never rewritten by a later cost change. cost_known = 0 where no cost
    -- was on record (every sale from before costing existed): those lines
    -- are left out of profit and margin.
    unit_cost_at_sale   REAL NOT NULL DEFAULT 0 CHECK (unit_cost_at_sale >= 0),
    cost_known          INTEGER NOT NULL DEFAULT 0 CHECK (cost_known IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_transaction_items_transaction_id
    ON transaction_items(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transaction_items_product_barcode
    ON transaction_items(product_barcode);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at);

-- Stock changes that are NOT a sale: depot_app receiving new inventory
-- or dispatching goods out (damaged/returned/transferred - anything
-- that isn't a POS checkout). Kept as its own audit trail, separate
-- from `transactions`, since these carry no money and a different
-- actor (warehouse staff, not a cashier) - see
-- database/inventory_repository.py. `quantity` is always stored
-- positive; `movement_type` says the direction.
CREATE TABLE IF NOT EXISTS stock_movements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_barcode     TEXT NOT NULL REFERENCES products(barcode),
    movement_type       TEXT NOT NULL CHECK (movement_type IN ('receive', 'dispatch')),
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    note                TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Where it happened and why (added with per-location stock - see
    -- stock_levels below; database/migrations.py adds these columns to a
    -- database created before them, where old rows keep NULLs).
    -- location_kind/location_code name the place whose stock moved;
    -- reason is 'receive' / 'dispatch' (depot Floor), 'shipment'
    -- (loaded onto / received from a shipment), 'transfer' (moved between
    -- two places), 'count' (a stock count corrected the level) or
    -- 'discrepancy' (a dealership received a different quantity than
    -- was shipped). reference is the other side: a shipment number, or
    -- the location a transfer came from / went to.
    location_kind       TEXT,
    location_code       TEXT,
    reason              TEXT,
    reference           TEXT,
    -- Who logged it ("Murat Yılmaz · B-100" snapshot, see shared/auth.Actor);
    -- NULL when nobody was signed in (the depot Floor kiosk) or for rows
    -- from before sign-in existed. Added by migration v2 on old databases.
    handled_by          TEXT
);

CREATE INDEX IF NOT EXISTS idx_stock_movements_product_barcode
    ON stock_movements(product_barcode);
CREATE INDEX IF NOT EXISTS idx_stock_movements_created_at
    ON stock_movements(created_at);

-- Dealership accounts (admin_app's Dealerships page). `code` is the
-- short account code the mockup itself treats as identity (e.g.
-- "CST-04") - kept UNIQUE and used as the lookup key by
-- database/dealership_repository.py, the same role `barcode` plays for
-- products. `manager_name` is plain text, not a FK - no employees table
-- exists to reference (see architecture.md's "New data domains" list).
-- Deliberately excludes fields the mockup itself fabricates client-side
-- (revenue/trend/roster/stock-on-hand) rather than persists - see
-- dealership_repository.py's module docstring.
CREATE TABLE IF NOT EXISTS dealerships (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    region       TEXT NOT NULL CHECK (region IN ('Metro', 'Coastal', 'Valley')),
    city         TEXT NOT NULL,
    manager_name TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_dealerships_region
    ON dealerships(region);

-- Employee/Workforce/Attendance domain (admin_app's Workforce page,
-- depot_app Console's Workforce Attendance tab). `badge_id` is the
-- natural key - what a badge scan/entry looks up, the same role `code`
-- plays for dealerships. `location_type`/`location_name` are plain text,
-- not FKs: warehouses aren't modeled as their own entity yet (only one
-- warehouse exists at all - see the "New data domains" note), and
-- dealerships are looked up by name, not id, to avoid a hard FK onto a
-- table that's still a young, editable v1 itself.
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    badge_id      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    title         TEXT,
    role          TEXT NOT NULL CHECK (role IN ('Operations', 'Logistics', 'Sales & service', 'Management')),
    location_type TEXT NOT NULL CHECK (location_type IN ('Warehouse', 'Dealership')),
    location_name TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- One row per check-in; `check_out_at` stays NULL while the employee is
-- on the floor (a NULL row is what "currently checked in" means - see
-- attendance_repository.list_open()). Real badge check-in/out, not the
-- mockup's fabricated weekly attendance-rate/absence percentages or its
-- shift-schedule calendar - see attendance_repository.py's docstring.
CREATE TABLE IF NOT EXISTS attendance_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id   INTEGER NOT NULL REFERENCES employees(id),
    check_in_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    check_out_at  TEXT,
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_employees_role
    ON employees(role);
CREATE INDEX IF NOT EXISTS idx_attendance_records_employee_id
    ON attendance_records(employee_id);
CREATE INDEX IF NOT EXISTS idx_attendance_records_check_in_at
    ON attendance_records(check_in_at);

-- Purchase Requests / Purchase Orders domain (depot_app Console's Manager
-- Portal > "Purchasing Operations" tab raises orders; admin_app's
-- Purchase Requests page approves/rejects the held ones and sets the
-- safe price bands). See database/purchase_order_repository.py.
--
-- One safe purchase price band per product, set by an administrator. An
-- order priced inside [min_unit_price, max_unit_price] is sent straight
-- to the supplier; anything outside it - or for a product with no band
-- at all - is held for admin approval. Per product rather than per
-- category (the admin Settings mockup's framing) because products have
-- no category field; the depot mockup's own form is per-SKU too.
-- `default_supplier` pre-fills the depot form's supplier field, like the
-- mockup's SKUS[sku].supplier.
CREATE TABLE IF NOT EXISTS purchase_price_ranges (
    product_barcode  TEXT PRIMARY KEY REFERENCES products(barcode) ON DELETE CASCADE,
    min_unit_price   REAL NOT NULL CHECK (min_unit_price >= 0),
    max_unit_price   REAL NOT NULL CHECK (max_unit_price >= min_unit_price),
    default_supplier TEXT,
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- A purchase order. `status` is the whole workflow:
--   'sent'     - placed with the supplier: either priced inside the band
--                (decided_at NULL) or approved by an admin (decided_at set)
--   'pending'  - held, awaiting an admin decision (`hold_reason` says why)
--   'rejected' - an admin declined it; never sent
--   'partially_received' / 'received' - goods have arrived against a sent
--                order (received_qty of quantity; migration v4)
--   'cancelled' - withdrawn before it was fully received; units already
--                received stay in stock
-- `product_name_at_order` and `range_min`/`range_max` are snapshots taken
-- at submission (same idea as transaction_items.unit_price_at_sale), so
-- the admin sees exactly what the order was judged against even if the
-- product or its band changes later. `site` is plain text, not a FK -
-- warehouses aren't their own table yet (same as employees.location_name).
CREATE TABLE IF NOT EXISTS purchase_orders (
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
    -- Who raised / decided it ("name · badge" snapshots; migration v2).
    raised_by             TEXT,
    decided_by            TEXT,
    -- Receiving / cancelling (migration v4): units delivered so far, when
    -- and by whom the latest delivery was booked, and who cancelled it.
    received_qty          INTEGER NOT NULL DEFAULT 0 CHECK (received_qty >= 0 AND received_qty <= quantity),
    received_at           TEXT,
    received_by           TEXT,
    cancelled_at          TEXT,
    cancelled_by          TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchase_orders_status
    ON purchase_orders(status);
CREATE INDEX IF NOT EXISTS idx_purchase_orders_created_at
    ON purchase_orders(created_at);

-- Treasury & Ledger domain (admin_app's Treasury & Ledger page; depot_app
-- Manager Portal > "02 Local Treasury & Ledger"). One row per financial
-- document: a check, promissory note, bank transfer, or invoice, either
-- coming IN (money owed to us - a receivable) or going OUT (money we owe -
-- a payable). See database/ledger_repository.py.
--
-- `status` is only what a person decides: 'pending' until it's settled,
-- then 'cleared' (paid / collected / cashed) or 'endorsed' (a check or
-- note we RECEIVED and passed on to someone else instead of cashing it).
-- "Overdue" is deliberately NOT stored: it's derived from due_date vs.
-- today (shared/treasury.py), because a stored "overdue" goes stale the
-- moment the date changes. issue_date/due_date are plain business dates
-- ('YYYY-MM-DD'), not timestamps. `amount` is always positive; `direction`
-- gives the sign. `site` is plain text like employees.location_name, or
-- NULL for a company-level document recorded in Admin. `detail` is the
-- mockups' second line under the counterparty: the bank, the note's term,
-- or the account ("First Coastal Bank", "90-day promissory note").
CREATE TABLE IF NOT EXISTS ledger_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    direction     TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    doc_type      TEXT NOT NULL CHECK (doc_type IN ('check', 'note', 'transfer', 'invoice')),
    doc_no        TEXT NOT NULL,
    counterparty  TEXT NOT NULL,
    detail        TEXT,
    site          TEXT,
    issue_date    TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    amount        REAL NOT NULL CHECK (amount > 0),
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'cleared', 'endorsed')),
    settled_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Who, as "name · badge" text snapshots (migration v5); NULL on rows
    -- from before auditing. The full story is in ledger_audit below.
    created_by    TEXT,
    settled_by    TEXT,
    updated_by    TEXT,
    CHECK (due_date >= issue_date),
    CHECK (status != 'endorsed' OR (direction = 'in' AND doc_type IN ('check', 'note'))),
    UNIQUE (direction, doc_type, doc_no)
);

CREATE INDEX IF NOT EXISTS idx_ledger_entries_due_date
    ON ledger_entries(due_date);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_site
    ON ledger_entries(site);

-- Append-only trail of every change to a ledger entry (migration v5): one
-- row per create / edit / clear / endorse / reopen / delete, written in the
-- SAME transaction as the change. entry_id is deliberately a plain integer
-- and NOT a foreign key: deleting an entry (allowed only while pending)
-- must leave its history behind. before_json / after_json are JSON
-- snapshots of the entry's fields (NULL before a create, NULL after a
-- delete). Triggers refuse UPDATE and DELETE, so history can't be rewritten.
CREATE TABLE IF NOT EXISTS ledger_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL,
    at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor_badge TEXT NOT NULL,
    actor_name  TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('created', 'edited', 'cleared', 'endorsed', 'reopened', 'deleted')),
    before_json TEXT,
    after_json  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_audit_entry
    ON ledger_audit(entry_id, id);

CREATE TRIGGER IF NOT EXISTS trg_ledger_audit_no_update BEFORE UPDATE ON ledger_audit
BEGIN SELECT RAISE(ABORT, 'ledger_audit is append-only'); END;

CREATE TRIGGER IF NOT EXISTS trg_ledger_audit_no_delete BEFORE DELETE ON ledger_audit
BEGIN SELECT RAISE(ABORT, 'ledger_audit is append-only'); END;

-- Shipment / Distribution domain: goods going from a warehouse to a
-- dealership. depot_app Console's Shipments page creates and dispatches
-- them, pos_app's Receive Inventory screen checks them in, admin_app's
-- Distribution page tracks them. See database/shipment_repository.py.
--
-- `status` is only what people did: 'scheduled' (planned / loading, not
-- left yet), 'in_transit' (dispatched), 'delivered' (received at the
-- dealership), 'cancelled'. "Arriving" and "Delayed" are derived from
-- eta/planned_eta vs. now (shared/distribution.py), never stored.
-- `planned_eta` is the ETA promised when the shipment was created; `eta`
-- is the current estimate (updated if it runs late) - the difference is
-- the mockup's "+2h 50m". Timestamps are UTC ISO strings, like created_at
-- everywhere else. `origin` is the warehouse site as plain text (warehouses
-- aren't a table yet); `dealership_code`/`dealership_name` are a snapshot,
-- not a FK, so deleting or renaming a dealership never rewrites history.
CREATE TABLE IF NOT EXISTS shipments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    origin           TEXT NOT NULL,
    dealership_code  TEXT NOT NULL,
    dealership_name  TEXT NOT NULL,
    carrier          TEXT NOT NULL,
    driver           TEXT,
    status           TEXT NOT NULL DEFAULT 'scheduled'
                     CHECK (status IN ('scheduled', 'in_transit', 'delivered', 'cancelled')),
    departed_at      TEXT,
    planned_eta      TEXT NOT NULL,
    eta              TEXT NOT NULL,
    delivered_at     TEXT,
    receipt_note     TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Per-location stock (added later; database/migrations.py adds both
    -- to older databases). origin_code: the warehouse whose stock the
    -- goods come out of (NULL on shipments planned before warehouses
    -- existed - those draw from unassigned stock). stock_moved: 1 while
    -- the goods are on the truck - taken off the origin's stock by
    -- dispatch, not yet received or cancelled - so cancel and receipt
    -- know where the units are.
    origin_code      TEXT,
    stock_moved      INTEGER NOT NULL DEFAULT 0 CHECK (stock_moved IN (0, 1))
);

-- One product per line. `received_qty` stays NULL until the dealership
-- completes its receipt; a value different from `expected_qty` is a
-- discrepancy (the POS mockup's "Report Discrepancy").
CREATE TABLE IF NOT EXISTS shipment_lines (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id          INTEGER NOT NULL REFERENCES shipments(id) ON DELETE CASCADE,
    product_barcode      TEXT NOT NULL REFERENCES products(barcode),
    product_name_at_ship TEXT NOT NULL,
    expected_qty         INTEGER NOT NULL CHECK (expected_qty > 0),
    received_qty         INTEGER CHECK (received_qty >= 0),
    UNIQUE (shipment_id, product_barcode)
);

CREATE INDEX IF NOT EXISTS idx_shipments_status
    ON shipments(status);
CREATE INDEX IF NOT EXISTS idx_shipments_dealership_code
    ON shipments(dealership_code);
CREATE INDEX IF NOT EXISTS idx_shipment_lines_shipment_id
    ON shipment_lines(shipment_id);
CREATE INDEX IF NOT EXISTS idx_shipment_lines_product_barcode
    ON shipment_lines(product_barcode);

-- Warehouse domain (admin_app's Warehouses page; each depot_app
-- instance IS one warehouse - the setup wizard asks for its details and
-- writes a Depot_<n>.warehouse.json sidecar, see
-- shared/warehouse_bootstrap.py). `code` (e.g. "WH-01") is the natural
-- key, the same role dealerships.code plays. `capacity_units` is how
-- many units of stock the building holds - NULL until someone sets it;
-- "used" is never stored, it's the live SUM of this warehouse's
-- stock_levels. `docks` is how many loading docks it has (the mockup's
-- "6/8" occupancy isn't tracked - nothing records which dock is busy).
CREATE TABLE IF NOT EXISTS warehouses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    city           TEXT NOT NULL DEFAULT '',
    capacity_units INTEGER CHECK (capacity_units IS NULL OR capacity_units > 0),
    docks          INTEGER NOT NULL DEFAULT 0 CHECK (docks >= 0),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Per-location stock: how many units of a product sit at one place. A
-- place is a warehouse (location_code = warehouses.code), a dealership
-- (= dealerships.code), or 'unassigned' (location_code = ''): stock
-- that existed before per-location tracking, or was entered in Admin
-- without a place, and hasn't been moved anywhere yet. The code is
-- plain text, not a FK, because it points at one of two tables.
-- database/stock_repository.py is the only place SQL for this lives.
--
-- products.stock_quantity stays as the company-wide total and always
-- equals SUM(stock_levels.quantity) + the units on shipments that have
-- been dispatched but not yet received (they've left the warehouse and
-- aren't at the dealership yet). Every write keeps both in step inside
-- one transaction.
CREATE TABLE IF NOT EXISTS stock_levels (
    location_kind   TEXT NOT NULL CHECK (location_kind IN ('warehouse', 'dealership', 'unassigned')),
    location_code   TEXT NOT NULL DEFAULT '',
    product_barcode TEXT NOT NULL REFERENCES products(barcode) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (location_kind, location_code, product_barcode)
);

CREATE INDEX IF NOT EXISTS idx_stock_levels_product_barcode
    ON stock_levels(product_barcode);
-- Indexes on columns that older databases only get from
-- database/migrations.py are created there, after the columns exist.

-- Sign-in accounts (see shared/auth.py, database/account_repository.py).
-- An account IS an employee (Workforce) given a role and a PIN; the badge
-- is what's typed at sign-in. `pin_hash` is a salted PBKDF2 hash, never
-- the PIN. failed_attempts / locked_until implement the lockout after
-- repeated wrong PINs. Deleting the employee deletes the account (the
-- repositories refuse to remove the last active administrator).
CREATE TABLE IF NOT EXISTS accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id      INTEGER NOT NULL UNIQUE REFERENCES employees(id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('admin', 'depot_manager', 'cashier')),
    pin_hash         TEXT NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    failed_attempts  INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    last_sign_in_at  TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- The sign-in audit trail (Admin > Settings > Sign-in activity): every
-- sign-in, sign-out, wrong PIN, lockout and account change, with the
-- badge typed (even when it matched no account), the part of the system
-- (admin / depot_console / pos) and the terminal. Plain text, no FKs, so
-- it survives the account being removed.
CREATE TABLE IF NOT EXISTS auth_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    badge_id    TEXT,
    event       TEXT NOT NULL,
    area        TEXT,
    terminal    TEXT,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_events_created_at
    ON auth_events(created_at);

-- Store-wide settings (Admin > Settings): store name and address for
-- receipts and reports, alert switches. Plain key/value text; what the
-- keys mean lives in shared/store_settings.py. Any app reads them, so a
-- name set in Admin shows on the till's receipts.
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
