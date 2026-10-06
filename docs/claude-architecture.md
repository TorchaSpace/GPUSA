# GPUSA — architecture quick map (for Claude)

Short, current orientation for an agent picking up this repo. The long narrative
(why each decision was made, mockup provenance) is in `../architecture.md` — read
the relevant section there before changing a domain, but note that some of it is
stale (see "Known stale spots" at the end). Last refreshed: 2026-10-06, at commit
`b4c6557`.

## What it is

Three PySide6 desktop apps over one shared SQLite file (`shared_backend.db`, WAL,
`busy_timeout`). No server, no IPC: cross-app "live" updates are `PollingTimer`
polls (10–15 s).

| App | Who | Shape | Theme |
|---|---|---|---|
| `pos_app` | cashier | header + 4 pages (Home, New Sale, Receive Inventory, My Local Stock); cashier sign-in, "Switch" at shift change | "Organic" (cream / terracotta, pill radii) |
| `depot_app` | warehouse staff | **Floor kiosk** is the main window (open, no sign-in). "İdari Giriş" → depot-manager sign-in → **Console** (separate window: Dashboard / Warehouses / Inventory / Shipments / Reports, 15-min idle lock) → **Manager Portal** dialog (PIN re-ask; Purchasing + Local Treasury, 10-min auto-lock) | "Industry" (radius 0, blueprint corner ticks, Barlow) |
| `admin_app` | manager | sidebar + `QStackedWidget`: Overview, Inventory, Dealerships, Workforce, Warehouses, Distribution, Purchase requests, Treasury & Ledger, Reports, Settings | "Classical" (dark, gold, serif) |

Language: English + Turkish everywhere. Currency: TRY/USD/EUR/GBP/none (store setting).

## Layering rules (enforced by convention — keep them)

```
shared/models.py      dataclasses, no I/O
database/             the ONLY place with SQL; repositories take/return shared.models
shared/ (rest)        pure logic, no storage/network; may not import database/
                      (exceptions: gui_kit/language_switch.py imports settings_repository lazily)
shared/builders/      pure document builders (receipt, sales report, movement report)
<app>/services/       thin app-side wrappers over repositories
<app>/gui/            widgets; never SQL
<app>/export/         I/O for builder output (print, PDF via reportlab, Excel via openpyxl)
```

- Apps never import each other. Similar widgets are duplicated per app on purpose.
- Every repository function uses `with connection_scope() as conn:`; writes use
  `BEGIN IMMEDIATE` and re-validate inside the lock.
- Status changes are `UPDATE … WHERE status IN (…)` → a lost race raises a typed
  error from `database/exceptions.py` (all subclass `DataAccessError`).
- History is snapshotted as text, not FKs: product name/price/cost at sale,
  dealership on shipments, "Name · badge" actor labels (`Actor.label`).

## Data model (`database/schema.sql` + `database/migrations.py`)

Tables: `products`, `transactions`, `transaction_items`, `stock_movements`,
`stock_levels`, `warehouses`, `dealerships`, `employees`, `attendance_records`,
`accounts`, `auth_events`, `purchase_price_ranges`, `purchase_orders`,
`ledger_entries`, `ledger_audit` (append-only via triggers), `shipments`,
`shipment_lines`, `app_settings` (key/value).

Migrations: `PRAGMA user_version`, `LATEST_VERSION = 6`, one `BEGIN IMMEDIATE` per
startup, safe when all three apps start at once. Schema.sql stays the source of
truth for new DBs; a new column needs **both** a schema.sql change and a `_to_vN`
step (+ bump `LATEST_VERSION`, + `tests/database/test_migration_vN.py`).

| v | Adds |
|---|---|
| 1 | per-location stock columns; old company stock → UNASSIGNED level |
| 2 | who-did-it: `handled_by`, `cashier`, `raised_by`, `decided_by` |
| 3 | `products.is_active`, case-insensitive unique barcode index |
| 4 | PO receiving (purchase_orders rebuilt for new CHECK), `products.cost_price`, `unit_cost_at_sale`, `cost_known` |
| 5 | ledger `created_by/settled_by/updated_by` + `ledger_audit` |
| 6 | `stock_movements.bin_code` (bin/dock no longer glued into `note`) |

### The stock invariant (most important rule)

```
products.stock_quantity = SUM(stock_levels) + units on dispatched, not-yet-received shipments
```

- `database/stock_repository.py` is the only writer of `stock_levels` and
  `stock_movements`. Locations: `StockLocation.warehouse(code)`,
  `.dealership(code)`, `UNASSIGNED`.
- Entering/leaving the company (Floor inbound/outbound, POS sale, count, receipt
  discrepancy, PO receipt) moves a level **and** the total; internal moves
  (transfer, loading a shipment) move levels only.
- `tests/stock_invariant.py` asserts it — call it in any new stock test.

## Domains → where the code lives

| Domain | Repository / pure logic | UI |
|---|---|---|
| Sales | `transaction_repository.finalize_transaction(txn, location, cashier)` re-checks price, product active, stock, cashier, dealership active inside the txn (`PriceChangedError`, …); `sale_cost_repository`, `shared/costing.py` (cents, weighted average cost, margin) | POS New Sale; Admin Reports/Overview show gross profit & "unknown cost" share |
| Stock / warehouses | `stock_repository`, `warehouse_repository`, `inventory_repository` (Floor facade), `shared/warehousing.py`, `warehouse_bootstrap.py` | Depot Floor + Console; Admin Warehouses (Move/count, Place all here) |
| Purchasing | `purchase_order_repository`: price bands, `submit` (in band → sent, else pending), `approve/reject`, `receive_against_order` (partial/full into a warehouse, updates weighted avg cost), `cancel_order`; `shared.models.hold_reason_for` | Depot Portal › Purchasing (+ `receive_delivery_dialog`); Admin Purchase requests |
| Treasury | `ledger_repository` (every write takes `actor`, writes `ledger_audit` in the same txn), `shared/treasury.py` (overdue etc. derived, never stored), `shared/ledger_audit.py` | Admin Treasury (History / Recent activity); Depot Portal › Local Treasury (record only, settle in Admin) |
| Shipments | `shipment_repository`, `shared/distribution.py` (progress/delay derived from schedule) | Depot Console › Shipments; POS Receive; Admin Distribution |
| People & sign-in | `employee_repository`, `attendance_repository`, `account_repository`, `shared/auth.py` (roles, PIN rules, PBKDF2 200k, lockout 5→5 min), `shared/current_session.py` | Admin Settings › Accounts; Depot Console › attendance + Floor Check-in Log |
| PIN recovery | `account_repository`: recovery code, security question, "start over" (backup beside DB → erase → new admin); `shared/recovery_code.py`, `shared/security_question.py` | Admin sign-in "Forgot your PIN or badge ID?" |
| Settings | `settings_repository` (`safe_language()`, `safe_currency()`, store profile, notification flags); `shared/store_settings.py` | Admin Settings; TR/EN switch in Depot headers |
| Setup / deploy | `shared/dealership_bootstrap.py`, `warehouse_bootstrap.py` (sidecar JSON + `applied_at` stamp), `shared/paths.py` (exe-adjacent `config.json` first, then ProgramData) | `deploy_system.py`, `build_setup.py` + `installer.py` (dependency-free), `packaging/build_release.py` (CI releases) |

Floor movements: no sign-in on the kiosk, but the form has an **operator badge**
field that credits the movement (`handled_by`), plus real ref and bin columns.
Check-in is scoped to this depot's warehouse. Floor header shows shift A/B/C
(`shared/shifts.py`).

## Cross-cutting UI machinery

- **i18n** — `shared/i18n.tr(key)`; falls back EN → key. Tables:
  `i18n.py` (base EN), `i18n_tr.py` (base + admin TR), `i18n_admin_en/tr.py`,
  `i18n_pos.py` (`EN_POS`/`TR_POS`), `i18n_depot.py` aggregating
  `i18n_depot_{floor,console,portal,pages}.py`. Helpers: `plural()`,
  `enum_label()`/`region_label()` (DB codes stay English), `LazyLabels`,
  `english()`, `UserError`.
  **PyInstaller trap:** new i18n modules must be imported statically *and* listed in
  each `.spec`'s `hiddenimports`, or packaged builds show raw keys.
- **Language change = restart.** Screens build their text once. Admin:
  `admin_app/gui/restart.py`; Depot: `shared/gui_kit/language_switch.py`
  (`LanguageSwitch`, injectable `restart`/`save` for tests).
- **Turkish upper-casing** — use `shared/textcase.upper()`, never `str.upper()` on
  UI text (İ/I). Tests look buttons up by their upper-cased label.
- **PDF fonts** — `shared/pdf_fonts.register_fonts()` (bundled DejaVu Sans in
  `shared/assets/fonts/`) so Turkish glyphs render.
- **Money** — `shared/currency.format_money()`; `shared/formatting` for
  amounts/parsing (`742,50` and `742.50` both accepted). Reject NaN/inf/huge.
- **Excel safety** — `shared/spreadsheet_safety.py` (formula injection) for exports.
- **Motion** — `shared/gui_kit/motion.py` (fade_in, count_up, HoverTween, Level,
  toast, dialog fade). `admin_app/gui/motion.py` is now just a re-export shim.
  Disabled on offscreen platform or `GPUSA_REDUCE_MOTION=1`. Hover filters must
  guard against widgets being torn down (`_alive`).
- **Theming** — `shared/theme.apply_theme(app, palette=…)`; each app owns its
  palette in `<app>/theme.py`. Use `#objectName` selectors; a selector-less
  `setStyleSheet` leaks borders onto child `QLabel`s. Escape `&` as `&&` in
  button/tab labels.
- `shared/gui_kit/`: `PollingTimer` (survives transient DB locks),
  `RefreshablePopup`, `VisualTab`, `sign_in_dialog`, `icon_kit.svg_to_icon`.

## Startup sequence (all three `main.py`s, roughly)

sidecar bootstrap (frozen builds only) → `QApplication` → `apply_theme` →
`i18n.set_language(settings_repository.safe_language())` →
`currency.set_currency(safe_currency())` → sign-in (Admin: first-admin screen if no
admin exists; POS: cashier) → main window. DB errors at sign-in show a dialog and
exit 1.

## Tests & CI

- `python -m pytest -q` (set `QT_QPA_PLATFORM=offscreen` headless). ~100 test files
  under `tests/{database,shared,admin_app,depot_app,pos_app}` + `test_setup_wizards.py`.
- `conftest.py` builds a fresh temp DB from `schema.sql` + `run_migrations`; never
  touch the real `shared_backend.db`. Helpers: `gui_support.py`, `po_support.py`,
  `ledger_support.py`, `stock_invariant.py`.
- When a business rule changes (checkout, ledger actor), test helpers must follow —
  commit `c61fefb` was exactly that kind of fix.
- CI (`.github/workflows/build.yml`): tests on Ubuntu/Windows/macOS, Python 3.12;
  tag `v*` or manual run builds Windows zip + macOS dmg/zip.

## Recent history (newest first, 2026-10-03 → 10-04)

1. `b4c6557` test fix: login button found by upper-cased label.
2. `72139db` Depot: "İdari Giriş" passed the `clicked(bool)` arg as a session →
   dead "Not signed in" console (buttons now ignore signal args); Floor no longer
   forces min height; TR|EN switch in Floor & Console headers.
3. `b1ec4b9` static depot i18n imports + spec `hiddenimports`; selectable currency;
   POS fully translatable; poller survives DB locks; dead POS stubs removed
   (`barcode_input`, `cart_view`, `stock_alert_banner`, POS `placeholder_page`).
4. `5abfdf1` Depot Turkish UI, real ref/bin columns (migration v6), operator
   credit, warehouse-scoped check-in, Console idle auto-lock, shift label, scan guards.
5. `c4b337e` Depot UI: real Check-in Log, big kiosk forms, hazard-stripe low-stock
   banner; motion moved to `shared/gui_kit/motion.py`.
6. `57d2464`, `929d130`, `2007d45`, `797d4cf` Admin motion/polish, language switch
   restarts the app, hover-filter teardown fix.
7. `1a93c6d` PO receiving, product cost & profit, ledger audit trail, sale
   integrity re-checks (migrations v4, v5).
8. `422c8e3` big audit pass: cent-exact reports, Turkish-safe PDF, amount
   validation, safe product/location delete, capacity/ETA checks, PIN rules, full
   Turkish Admin.
9. `2415980`…`eb81a99` Forgot-PIN flow (recovery code, security question, start over).

## Known stale spots in `architecture.md`

- "Floor passes no actor" — now the Floor has an operator badge field.
- "ref and bin combined into `note`" — separate `reference` / `bin_code` since v6.
- PO section "Not in this v1: receiving against a PO" — done (`receive_against_order`).
- "Transaction has no cost" / sales profit absent — done (v4).
- Console idle lock and TR/EN switch not described.
- `admin_app/gui/product_management_tab.py`, `price_update_tab.py`,
  `inventory_health_tab.py` are still unwired leftovers (only `sales_reports_tab.py`
  is reachable) — verify before relying on them.

## Still open / not built

Payment method on transactions (Card/Cash finalize identically), bins/put-away as
a real entity, per-location reorder levels, warehouse-to-warehouse truck
transfers, multi-drop / partial shipments, partial ledger payments & bank
balances, idle lock for Admin/POS, real webfonts (system fallbacks today).
