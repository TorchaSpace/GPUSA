"""Single source of truth for shared constants.

Colors, fonts, spacing, page sizes, and the shared database location all
live here, once each. No module outside this file should redefine a
color literal, a font name, or the database filename - import it from
here instead, so changing the brand palette or the DB location never
means hunting through every file for a copy.

Note: pos_app and admin_app each have their OWN component-level sizing
(button dimensions, touch-target sizes, table row height, etc.) inside
their own gui/components/ packages, since their ergonomic needs are
deliberately different (POS = large fast-action targets, Admin =
compact/dense). Only the base palette/typography tokens are shared.
"""

from pathlib import Path

# --- Project layout -----------------------------------------------------

# Only meaningful when running from source (not a frozen .exe) - see
# shared/paths.py for the frozen-aware resolution both apps actually use
# for bundled resources and the shared database location.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATABASE_FILENAME = "shared_backend.db"

# Name of the single, machine-wide folder (under %ProgramData% on
# Windows) where both installed apps keep shared_backend.db and their
# shared config.json - see shared/paths.get_shared_data_dir(). Rename
# this once you have real branding; changing it after either app has
# shipped means existing installs won't find their old data.
APP_DATA_DIR_NAME = "POSInventorySystem"

# --- Store identity (used on receipts and report letterheads) ----------

STORE_NAME = "TODO: set store name"
STORE_ADDRESS_LINES: list[str] = []

# --- Typography -----------------------------------------------------------

FONT_FAMILY = "Segoe UI"
FONT_FAMILY_MONOSPACE = "Consolas"  # used for the receipt builder

FONT_SIZE_SMALL = 11
FONT_SIZE_BASE = 13
FONT_SIZE_LARGE = 18
FONT_SIZE_XL = 24

# --- Spacing scale (px) ---------------------------------------------------

SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 16
SPACING_LG = 24
SPACING_XL = 32

# --- Base color tokens (see shared/theme.py for the full dark/light palettes) --

COLOR_ALERT_CRITICAL = "#E5484D"   # low stock / destructive actions
COLOR_ALERT_SUCCESS = "#3DD68C"    # complete sale / confirmations

# --- Export / document sizing --------------------------------------------

RECEIPT_WIDTH_CHARS = 42  # standard 80mm thermal printer at this font size
REPORT_PAGE_SIZE = "A4"

# --- Alert polling (see shared/gui_kit/polling.py) ------------------------
# SQLite has no cross-process push notification, so "real-time" low-stock
# alerts are actually short-interval polling. depot_app's alert panel is
# the primary consumer; admin_app's passive Inventory Health tab can use
# the same interval (or a longer one - it's not time-critical there).

CRITICAL_STOCK_POLL_INTERVAL_MS = 15_000  # 15s: fast enough to feel live, gentle on WAL readers
# admin_app's Purchase requests badge/page: one indexed COUNT(*) per tick.
PURCHASE_REQUEST_POLL_INTERVAL_MS = 10_000
# Shipment screens (depot Shipments, POS Receive, admin Distribution): a
# dealership's receipt or a depot's dispatch shows up elsewhere this fast.
SHIPMENT_POLL_INTERVAL_MS = 15_000
# admin_app's Warehouses page: capacity, movement and on-shift numbers
# change with every depot/POS action, so the page re-reads while open.
WAREHOUSE_POLL_INTERVAL_MS = 15_000
