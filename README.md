# POS & Inventory Management System

Three PySide6 desktop apps sharing one SQLite backend (`shared_backend.db`,
WAL mode) through a common data-access layer (`database/`):

- `pos_app/` - cashier checkout
- `depot_app/` - warehouse receiving/dispatch, and the primary home for
  critical-stock alerts
- `admin_app/` - management: products, pricing, sales reports, and a
  passive inventory-health view

See `architecture.md` for the full design rationale, including why
low-stock alerts are Depot's job and not Admin's.

## Setup

```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## Running

```
python -m pos_app.main
python -m depot_app.main
python -m admin_app.main
```

All three apps create/open `shared_backend.db` under `%ProgramData%\POSInventorySystem\`
on first run - see `shared/paths.py` for how they agree on that location
regardless of where each was installed.

**Signing in.** Open Admin first: on a fresh system it asks you to create
the first administrator (your name, a badge ID, a PIN of 6+ digits). Then,
in Admin > Settings, give employees from Workforce an account - Cashier
(signs in at a POS till), Depot manager (opens a depot's Manager Console
from the Floor's "İdari Giriş") or Administrator. The POS asks "Who's on
the till?" before it opens (the header's "Switch" hands over at shift
change); the depot Floor stays open to everyone. Five wrong PINs lock an
account for 5 minutes; an administrator can unlock it in Settings.
Admin and the POS sign out by themselves after 15 minutes with nobody at
the screen (the POS asks "Who's on the till?" again, as at a shift change).

## Testing

```
pytest
```

GUI tests use `pytest-qt` and skip cleanly with no display available.
Tests never touch the real `shared_backend.db` - see `tests/conftest.py`.

## Downloads (Windows, macOS and Linux)

Pushing a version tag builds every download on GitHub and publishes them
on the repo's Releases page - nothing to build by hand:

```
git tag v0.1.0
git push --tags
```

| File | For |
|---|---|
| `GPUSA-macOS-arm64.dmg` / `.zip` | Macs with Apple Silicon (M1 and newer) |
| `GPUSA-macOS-x64.dmg` / `.zip` | Intel Macs |
| `GPUSA-Windows-x64.zip` | Windows 10/11 |
| `GPUSA-Linux-x64.tar.gz` | 64-bit Linux: Arch, Ubuntu 22.04+, Fedora, Debian 12+ … (unpack, optionally run `install.sh` for menu entries) |

`docs/index.html` is a download page that picks the right one for the
visitor's computer (turn on GitHub Pages for the `docs/` folder to publish
it; it needs a public repo, or just link people to the Releases page). The
actions can also be run by hand from the Actions tab ("Run workflow",
choosing all platforms or just one, e.g. `linux`) - the files are then
attached to that run instead of a release.

To build one yourself on the machine you're on (PyInstaller can't build for
the other OS): `pip install -r requirements.txt`, then
`python packaging/build_release.py` -> `release/`.

The apps aren't code-signed yet, so the first launch needs one
confirmation on each OS (the steps are in `INSTALL.txt` inside every
download). Where the shared database lives by default: Windows
`C:\ProgramData\POSInventorySystem`, macOS
`~/Library/Application Support/POSInventorySystem`, Linux
`~/.local/share/POSInventorySystem`.

## Building a Windows .exe + installer (Windows only)

Each app packages independently. From the repo root (or anywhere - the
scripts locate the repo root themselves):

```
pos_app\build_exe.bat        REM -> pos_app\dist\BranchPOS\BranchPOS.exe
depot_app\build_exe.bat      REM -> depot_app\dist\DepotApp\DepotApp.exe
admin_app\build_exe.bat      REM -> admin_app\dist\AdminDashboard\AdminDashboard.exe
```

Then compile the matching Inno Setup script (requires
[Inno Setup](https://jrsoftware.org/isinfo.php) - a free Windows tool,
separate from anything `pip install`s) to produce a one-click installer:

```
iscc pos_app\installer\pos_app_installer.iss        REM -> pos_app\installer_output\
iscc depot_app\installer\depot_app_installer.iss    REM -> depot_app\installer_output\
iscc admin_app\installer\admin_app_installer.iss    REM -> admin_app\installer_output\
```

All three installers run with `PrivilegesRequired=lowest` (no UAC prompt)
and install/launch with a couple of clicks - see `architecture.md`'s
Packaging section for the full rationale, including how three
independently-installed apps still find the same shared database.

## Mass-deploying multiple instances (deploy_system.py)

For rolling out several tills/workstations at once without an installer
per machine, `deploy_system.py` is a second, separate build path: an
interactive wizard that builds each app once as a single-file `.exe`
(PyInstaller `--onefile --noconsole`) and copies it as many times as you
ask for into one flat `dist/` folder, alongside one shared `config.json`:

```
python deploy_system.py
```

It'll ask how many Admin/POS/Depot instances you want and what database
path to share, then produce e.g. `dist/POS_1.exe`, `dist/POS_2.exe`,
`dist/Depot_1.exe`, `dist/Admin_1.exe`, `dist/config.json`. To roll one
out, copy its `.exe` **and** `config.json` together onto the target
machine - each `.exe` reads `config.json` from its own folder at
startup (see `shared/paths.py`). This doesn't install anything (no Start
Menu entry, no uninstaller) and doesn't replace the per-app installers
above - use whichever fits how you're actually distributing machines.

**Every POS instance also becomes a dealership automatically.** Right
after the POS exes are built, you'll be asked whether to type real
dealership details (code/name/region/city/manager - the same fields
Admin's Dealerships page edits) for each one, or just accept an
auto-filled placeholder. Either way, each `POS_N.exe` gets a matching
`dist/POS_N.dealership.json`. **Opening `Admin_1.exe` from that same
folder is enough** - Admin applies every POS sidecar beside it on startup,
so all the terminals from that setup run appear as active dealerships
immediately, without launching each POS first. (A POS instance moved to
its own till applies its own sidecar on first launch instead - take the
file along with `config.json`.) Re-running setup with new details for an
existing code updates that dealership; edits you make afterward in
Admin > Dealerships are kept. See `architecture.md`'s "A generated POS
terminal registers itself as a dealership" section.

**Every Depot instance also becomes a warehouse.** Right after the Depot
exes, you'll be asked whether to type each warehouse's details (code /
name / city / capacity in units / loading docks) or accept WH-01, WH-02, …
placeholders; each `Depot_N.exe` gets a `Depot_N.warehouse.json`, which
Admin (from the same folder) or that Depot turns into an Admin >
Warehouses entry - same rules as the dealership sidecars, outcomes in
`Depot_N.warehouse.log`. The Depot then works on that warehouse's stock.

**Terminal not showing up as active in Admin?** Check
`POS_N.dealership.log` next to it - every attempt (by Admin or by the POS
itself) records what happened and which database file it used. If that
path differs from the `db_path` in the `config.json` of the Admin you're
looking at, the two are reading different databases.

## Handing off to someone else (build_setup.py -> setup.exe)

`deploy_system.py` above is great for your own machine, but it needs
this repo, Python, and PyInstaller present to run - not something to
send to a branch manager. `build_setup.py` solves that by packaging
*everything* - all three apps, plus the wizard itself - into one
standalone `setup.exe` that needs nothing else alongside it:

```
python build_setup.py
```

This builds `admin_app`, `pos_app`, and `depot_app` as onefile exes,
then compiles `installer.py` (the interactive wizard) into
`Kurulum/setup.exe`, embedding all three app exes inside it. That's
the one file to hand off - see `architecture.md`'s "Two-stage installer"
section for exactly how it finds its embedded payload.

**What the person you send it to does:** drop `Kurulum/setup.exe`
into an empty folder on the target machine and double-click it. It asks
how many Admin/POS/Depot instances to install and what shared database
path to use (read its prompt carefully if multiple machines need to
share one database - pressing Enter defaults each machine to its own
local database), then writes the requested `.exe`s plus one
`config.json` into that same folder. No Python, no installer, nothing
else required on that machine. If any POS instances are installed, it
also asks whether to type real dealership details for each one (same
prompt and same fields as `deploy_system.py`'s, above) and writes a
matching `POS_N.dealership.json` beside each `POS_N.exe` - open the
`Admin_1.exe` it installed in that same folder and every one of those
terminals is already listed as an active dealership.

Never run `installer.py` directly with `python installer.py` - it has
no embedded payload to unpack unless it's running as the compiled
`setup.exe`; it's dependency-free by design so it can be compiled and
run completely outside this repo.

### Skipping the `python build_setup.py` command: build.exe

If you'd rather double-click something than type a command, compile
`build_setup.py` itself into `build.exe` once:

```
make_build_exe.bat
```

From then on, running `build.exe` (sitting at the repo root) does
exactly the same thing as `python build_setup.py` - it's a thin
convenience wrapper, not a different tool. **It still needs a `python`
on PATH and this same repo checkout present** - unlike `setup.exe`,
`build.exe` is not standalone, since it has to invoke PyInstaller itself
to build everything. If PySide6 or PyInstaller aren't installed for that
`python` yet, `build.exe` runs `pip install -r requirements.txt`
automatically before giving up - you don't need to `pip install` by hand
first, just have a `python` on PATH with network access to PyPI. Re-run
`make_build_exe.bat` any time `build_setup.py` changes; `build.exe`
doesn't update itself.

So the full picture is two source scripts and two compiled tools, one
building the other: `build_setup.py` (optionally compiled to `build.exe`)
produces `Kurulum/setup.exe`, which is what actually gets handed out.

## Status

The module layout, data models, theming, i18n, and the WAL-mode
connection layer are functional.

**All three apps are now rebuilt on real UI mockups** Erol supplied
(Claude Design handoff bundles) - each app has its own visual identity
(`admin_app/theme.py`'s dark-serif "Classical" palette,
`pos_app/theme.py`'s warm-rounded "Organic" palette,
`depot_app/theme.py`'s light "Industry" blueprint palette) and a screen
layout matching those mockups, in place of the original generic tab
layouts. See `architecture.md`'s "UI redesign" section for the full
breakdown of what's real vs. placeholder per screen. In short:

- **`admin_app`**: a 10-page sidebar (Overview, Inventory, Dealerships,
  and Workforce are real - Dealerships wired to a new
  `dealership_repository`, its own table, a v1 domain deliberately
  scoped to the mockup's actual persisted fields, not its fabricated
  revenue/trend/roster numbers; Workforce wired to new
  `employee_repository`/`attendance_repository` tables, showing today's
  real roster/attendance status rather than the mockup's fabricated
  weekly schedule and attendance-rate numbers; Purchase requests wired
  to `purchase_order_repository` - approve/reject held orders, set each
  product's safe price band, full order history, and a live pending-count
  badge in the sidebar; Treasury & Ledger wired to `ledger_repository` -
  received/issued checks, promissory notes, transfers and invoices with
  receivables/payables totals, a 30-day due-date strip, and clear/endorse/
  reopen; Distribution wired to `shipment_repository` - in-transit KPIs,
  a route tracker, the live transit ledger and delivered-with-discrepancy
  history; Warehouses wired to `warehouse_repository`/`stock_repository` -
  capacity cards (85% threshold), movement logs, workforce per site, stock
  by location, move / count stock; Settings - sign-in accounts (add,
  reset PIN, role, switch off, unlock) and the sign-in activity log;
  Reports - revenue trend, region and payment-method split, dealership
  ranking, CSV/PDF/Excel export, with the per-product Sales Reports one
  button away). Run `python -m admin_app.main`.
- **`pos_app`**: a 4-screen header nav (Home, New Sale, My Local Stock
  and Receive Inventory are all real and wired to the same shared
  database - stock shown and sold is THIS dealership's shelf; Receive
  checks in shipments sent to this terminal's dealership, with Accept
  All / Report Discrepancy, and puts them on that shelf). New Sale's checkout is a real,
  atomic sale that records the signed-in cashier - see below. Run `python -m pos_app.main`.
- **`depot_app`**: NOT a sidebar app - its primary/default window is a
  distraction-free Floor kiosk (low-stock alert banner + real Inbound/
  Outbound receive-dispatch logging; a Check-in Log panel is a themed
  placeholder). Its "İdari Giriş" button asks a depot manager to sign in
  and opens a separate Manager Console window (sidebar nav, all real:
  Dashboard - this warehouse's KPIs, low stock, shipments leaving, latest
  activity; Warehouses - one card per warehouse with capacity and today's
  in/out, this depot's movement log, and the Workforce Attendance tab;
  Inventory - this warehouse's stock with search/filters and stock counts;
  Shipments; Reports - stock movements over a period with Excel/PDF
  export), whose own "İdari Giriş" asks that manager's PIN again and opens a
  Manager Portal modal whose Purchasing Operations tab is real - raise
  purchase orders; ones priced inside the admin-set safe band go straight
  to the supplier, the rest wait for approval in admin_app - and whose
  Local Treasury & Ledger tab shows (and records) this depot's checks,
  notes, payments and receivables. Run `python -m depot_app.main`.

**`database/transaction_repository.py`'s `finalize_transaction()` and
`get_by_id()` are implemented** - a real, atomic checkout: validates
stock, writes the transaction + line items, and decrements stock, all in
one `BEGIN IMMEDIATE` transaction, raising `InsufficientStockError`/
`ProductNotFoundError` and writing nothing on failure.
`shared/builders/receipt_builder.py` and `pos_app/export/receipt_printer.py`
are implemented too (a real plain-text receipt via `QPrinter`; a failed
physical print never undoes an already-committed sale).

**`database/inventory_repository.py`'s `receive_stock()`,
`dispatch_stock()`, and `list_recent_movements()` are implemented** too -
same `BEGIN IMMEDIATE` atomic-write pattern as `finalize_transaction()`,
backing depot_app's real Inbound/Outbound panels and Console's read-only
movement feed.

**`database/employee_repository.py` and `database/attendance_repository.py`
are implemented** - `employee_repository` is plain CRUD (mirrors
`dealership_repository`'s shape); `attendance_repository`'s `check_in()`/
`check_out()` use the same `BEGIN IMMEDIATE` atomic pattern, backing
admin_app's Workforce page and depot_app Console's Workforce Attendance
tab. See `architecture.md`'s Workforce bullet for what's real (badge
check-in/out, today's roster) vs. deliberately dropped (a fabricated
weekly shift schedule and attendance-rate/absence trend).

**`database/purchase_order_repository.py` is implemented** - per-product
safe price bands, `submit()` (decides send-vs-hold inside `BEGIN
IMMEDIATE`, using the same `shared.models.hold_reason_for()` rule the
depot form previews with), and `approve()`/`reject()` that refuse a
second decision on an already-decided order. Backs depot_app's Manager
Portal > Purchasing Operations and admin_app's Purchase requests page.
To try it: in Admin > Purchase requests, "Set range" for a product; in
Depot, open the Console, then its "İdari Giriş", and order that product
at a price outside the range - it appears in Admin's queue (and the
sidebar badge) within about ten seconds. See `architecture.md`'s
"Purchase Requests / Purchase Orders" section.

**`database/ledger_repository.py` is implemented** - checks, promissory
notes, transfers and invoices, in or out, with clear / endorse / reopen.
"Overdue", the receivables/payables totals and the 30-day milestone strip
are computed by `shared/treasury.py`, never stored, so they're always
right for today. Backs admin_app's Treasury & Ledger page and depot_app's
Manager Portal > Local Treasury & Ledger. See `architecture.md`'s
"Treasury & Ledger" section.

**`database/shipment_repository.py` is implemented** - the depot's
Console > Shipments plans and dispatches shipments to dealerships, each
dealership's POS Receive Inventory checks them in, admin_app's
Distribution page tracks them. Dispatch takes the goods off the depot's
warehouse, the receipt puts them on the dealership's shelf - so don't also
log a shipment as an Outbound dispatch on the Floor. See `architecture.md`'s
"Shipments / Distribution" section.

**Per-location stock (`database/stock_repository.py`, `warehouse_repository.py`,
`migrations.py`) is implemented** - every warehouse and dealership holds its
own stock; `products.stock_quantity` is the company total (all locations +
what's on trucks). **After upgrading, existing stock shows as "Unassigned"**
(nothing ever recorded where it physically is): open Admin > Warehouses and
use **Place all here**, or **Move / count stock** to split it - until then
dealerships have nothing on their shelves to sell. See `architecture.md`'s
"Warehouses & per-location stock" section.

**Card / Cash are recorded** (migration v7, `transactions.payment_method`):
the receipt prints how the sale was paid and Admin > Reports' export splits
revenue by payment method, so the till can be counted at the end of a day.
Sales from before the upgrade show as "Not recorded".

**Automatic daily backups** (`database/backups.py`): every app, when it
opens and then once an hour, makes sure today's copy exists in a `backups`
folder beside `shared_backend.db` (`shared_backend-YYYY-MM-DD.db`); the
newest 14 are kept. Restoring is copying one back over `shared_backend.db`
with every app closed. Admin > Settings can still write an extra copy anywhere.
