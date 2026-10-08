"""Plain data classes shared by every layer.

These are pure data - no I/O, no SQL, no Qt imports. `database/`
repositories return these; `pos_app` and `admin_app` pass them around;
`shared/builders` consumes them to produce receipts and reports. Defining
Product and Transaction here (instead of inside database/) is what lets
GUI code reference "what a product is" without importing the data access
layer at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Product:
    barcode: str
    name: str
    price: float
    stock_quantity: int
    critical_stock_level: int = 0
    # Deactivated products stay in history and in Admin (dimmed) but can't be
    # sold, received or put on a shipment. (products.is_active, migration v3.)
    is_active: bool = True
    # Only meaningful on rows from stock_repository.products_at/product_at:
    # whether that location has (or had) a stock level row for the product.
    # A location is never alerted about a product it has never stocked.
    stocked_here: bool = True
    # Weighted-average unit cost (products.cost_price, migration v4); 0 = not
    # known yet. Kept up to date by purchase-order receipts, or typed in Admin.
    cost_price: float = 0.0

    @property
    def cost_known(self) -> bool:
        return self.cost_price > 0

    @property
    def is_below_critical_stock(self) -> bool:
        """At/below the reorder level. A reorder level of 0 means "none
        set" - such a product (a brand-new one with no stock, say) is never
        flagged; "out of stock" is a separate status."""
        return self.critical_stock_level > 0 and self.stock_quantity <= self.critical_stock_level


@dataclass
class LineItem:
    """One product/quantity pairing within a Transaction.

    `unit_price_at_sale` and `product_name_at_sale` are captured at
    checkout time (see database/transaction_repository.py), not looked
    up live from `products`, so a later price or name change never
    rewrites the history of a past sale.
    """

    product_barcode: str
    product_name_at_sale: str
    unit_price_at_sale: float
    quantity: int
    # What one unit cost us when it was sold (transaction_items.unit_cost_at_sale,
    # migration v4). `cost_known` False = no cost was on record then (every
    # sale made before costing existed): such a line is left out of profit.
    unit_cost_at_sale: float = 0.0
    cost_known: bool = False

    @property
    def line_total(self) -> float:
        return round(self.unit_price_at_sale * self.quantity, 2)


# How a sale was paid - matches database/schema.sql's payment_method CHECK.
PAYMENT_METHODS = ("card", "cash")


@dataclass
class Transaction:
    items: list[LineItem] = field(default_factory=list)
    id: int | None = None
    created_at: datetime | None = None
    dealership_code: str | None = None  # whose shelf it came off (None: unassigned stock)
    cashier: str | None = None  # "name · badge" of the signed-in cashier
    payment_method: str | None = None  # one of PAYMENT_METHODS; None on sales from before v7
    # A key the till makes once per sale (migration v9): sending the same sale
    # again - a retry after a dropped connection, a re-upload from an offline
    # till - returns the sale already stored instead of selling twice.
    client_uuid: str | None = None

    @property
    def total(self) -> float:
        return round(sum(item.line_total for item in self.items), 2)


# The three regions the Dealerships mockup's own filter dropdown offers -
# matches database/schema.sql's `region` CHECK constraint exactly. Shared
# here so both dealership_repository.py (pre-insert validation, a clearer
# error than a raw sqlite3 CHECK failure) and admin_app's GUI (populating
# the region combo box) read from one list rather than each hardcoding it.
DEALERSHIP_REGIONS = ("Metro", "Coastal", "Valley")


@dataclass
class Dealership:
    """A dealership account (admin_app's Dealerships page).

    `code` (e.g. "CST-04") is the natural/business key - the mockup's own
    identity field, and what dealership_repository.py's functions look
    up/act on, the same role `barcode` plays for Product. `id` is the
    surrogate primary key SQLite assigns on create() and isn't meant to
    be typed by a user; it's None until then. Deliberately has no
    revenue/trend/roster/stock-on-hand fields - the mockup fabricates all
    of those client-side rather than persisting them (see
    dealership_repository.py's module docstring).
    """

    code: str
    name: str
    region: str
    city: str
    manager_name: str | None = None
    is_active: bool = True
    id: int | None = None


# The Workforce mockup's own role/location filters - matches
# database/schema.sql's `role`/`location_type` CHECK constraints exactly.
# Shared here so employee_repository.py (validation) and admin_app's GUI
# (combo boxes) both read from one list, same convention as
# DEALERSHIP_REGIONS above.
EMPLOYEE_ROLES = ("Operations", "Logistics", "Sales & service", "Management")
EMPLOYEE_LOCATION_TYPES = ("Warehouse", "Dealership")


@dataclass
class Employee:
    """An employee record (admin_app's Workforce page, depot_app
    Console's Workforce Attendance tab). `badge_id` is the natural/
    business key - what a badge scan or manual entry looks up, the same
    role `code` plays for Dealership. `location_type`/`location_name`
    are plain text, not a FK onto a warehouses or dealerships table -
    see schema.sql's comment. Deliberately has no weekly-hours-target,
    shift-schedule, or attendance-rate fields - the mockup fabricates
    all of those client-side rather than persisting them (see
    attendance_repository.py's module docstring).
    """

    badge_id: str
    name: str
    role: str
    location_type: str
    location_name: str
    title: str | None = None
    is_active: bool = True
    id: int | None = None


@dataclass
class AttendanceRecord:
    """One check-in/check-out cycle for an employee. `check_out_at` is
    None while the employee is still checked in - see
    attendance_repository.list_open()."""

    employee_id: int
    check_in_at: str
    check_out_at: str | None = None
    note: str | None = None
    id: int | None = None


# --- Purchase Requests / Purchase Orders ---------------------------------
#
# Matches database/schema.sql's purchase_orders CHECK constraints exactly.
# See database/purchase_order_repository.py for the workflow.
PURCHASE_ORDER_STATUSES = ("pending", "sent", "rejected", "received", "partially_received", "cancelled")
# Statuses an order can still take goods in (an approved / sent order, or one already part-delivered).
RECEIVABLE_STATUSES = ("sent", "partially_received")
HOLD_REASONS = ("above_range", "below_range", "no_range")


@dataclass
class PriceRange:
    """An administrator-set safe purchase price band for one product.
    Orders priced inside [min_unit_price, max_unit_price] go straight to
    the supplier; anything outside is held for admin approval."""

    product_barcode: str
    min_unit_price: float
    max_unit_price: float
    default_supplier: str | None = None


def hold_reason_for(price_range: PriceRange | None, unit_price: float) -> str | None:
    """Why an order at `unit_price` must be held for admin approval, or
    None if it can be sent directly. The single rule both the depot form's
    live preview and purchase_order_repository.submit() (the authoritative
    check) use, so the two can never disagree:
      - no band set for the product -> "no_range" (held: nobody has
        said what a sane price is yet, so an admin has to look)
      - above the maximum          -> "above_range"
      - below the minimum          -> "below_range" (usually a unit or
        quantity typo - the admin Settings mockup's "flag requests below
        the minimum" rule; the depot mockup holds these too)
    The band's ends are inclusive.
    """
    if price_range is None:
        return "no_range"
    if unit_price > price_range.max_unit_price:
        return "above_range"
    if unit_price < price_range.min_unit_price:
        return "below_range"
    return None


@dataclass
class PurchaseOrder:
    """One purchase order raised from depot_app's Manager Portal.

    `status` is "sent" (placed with the supplier - automatically, or
    after admin approval when `decided_at` is set), "pending" (held for
    admin approval, see `hold_reason`), "rejected", then once goods
    arrive "partially_received" / "received", or "cancelled". `product_name` and
    `range_min`/`range_max` are snapshots from submission time.
    Timestamps are the database's UTC ISO strings.
    """

    product_barcode: str
    product_name: str
    supplier: str
    quantity: int
    unit_price: float
    site: str
    status: str
    hold_reason: str | None = None
    range_min: float | None = None
    range_max: float | None = None
    id: int | None = None
    created_at: str | None = None
    decided_at: str | None = None
    decision_note: str | None = None
    raised_by: str | None = None  # "name · badge" of who raised it (None: before sign-in existed)
    decided_by: str | None = None  # "name · badge" of the admin who approved/rejected it
    # Receiving (migration v4): units delivered so far, and who/when.
    received_qty: int = 0
    received_at: str | None = None  # the latest delivery
    received_by: str | None = None
    cancelled_at: str | None = None
    cancelled_by: str | None = None

    @property
    def number(self) -> str:
        """Human-facing order number, e.g. "PO-00042" (derived from id)."""
        return f"PO-{self.id:05d}" if self.id is not None else "PO-(new)"

    @property
    def total(self) -> float:
        """unit_price x quantity, rounded half-up to cents (via Decimal, so
        0.125 x 1 is 0.13 and float noise like 1.005 x 3 can't tip it)."""
        from decimal import ROUND_HALF_UP, Decimal

        exact = Decimal(str(self.unit_price)) * Decimal(self.quantity)
        return float(exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @property
    def was_approved(self) -> bool:
        """Held, then approved by an admin (it may since have been delivered)."""
        return self.status in ("sent", "partially_received", "received") and self.decided_at is not None

    @property
    def raised_by_badge(self) -> str:
        """The badge in the "name · badge" snapshot of who raised the order,
        upper-case ("" when nobody was signed in or it predates sign-in)."""
        _, dot, badge = (self.raised_by or "").rpartition("·")
        return badge.strip().upper() if dot else ""

    @property
    def remaining_qty(self) -> int:
        """Units still due: 0 once received in full, and for a cancelled /
        rejected / still-pending order (nothing more can be taken in)."""
        return max(0, self.quantity - self.received_qty) if self.status in RECEIVABLE_STATUSES else 0

    @property
    def can_receive(self) -> bool:
        return self.status in RECEIVABLE_STATUSES and self.remaining_qty > 0

    @property
    def can_be_cancelled_by_admin(self) -> bool:
        return self.status in ("pending", "sent", "partially_received")


# --- Treasury & Ledger ---------------------------------------------------
#
# Match database/schema.sql's ledger_entries CHECK constraints exactly.
LEDGER_DIRECTIONS = ("in", "out")
LEDGER_DOC_TYPES = ("check", "note", "transfer", "invoice")
LEDGER_STATUSES = ("pending", "cleared", "endorsed")

# Display names. Admin's mockup says Check / Note / Transfer; the depot
# mockup calls an outgoing transfer a "Payment" and an incoming invoice a
# "Receivable" - see depot_app/gui/treasury_panel.py for that mapping.
LEDGER_DOC_TYPE_LABELS = {
    "check": "Check",
    "note": "Promissory note",
    "transfer": "Transfer",
    "invoice": "Invoice",
}


@dataclass
class LedgerEntry:
    """One financial document: a check, promissory note, transfer or
    invoice, coming in (receivable) or going out (payable).

    `status` is "pending" until settled, then "cleared" or "endorsed"
    (a received check/note passed on instead of cashed). Whether it's
    overdue is derived from `due_date`, never stored - see
    shared/treasury.py. `amount` is always positive; `signed_amount`
    applies the direction.
    """

    direction: str
    doc_type: str
    doc_no: str
    counterparty: str
    issue_date: date
    due_date: date
    amount: float
    detail: str | None = None
    site: str | None = None
    status: str = "pending"
    id: int | None = None
    settled_at: str | None = None
    created_at: str | None = None
    # Who did it ("name · badge" text snapshots; None on rows from before
    # the audit trail existed). The full history is ledger_audit.
    created_by: str | None = None
    settled_by: str | None = None
    updated_by: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == "pending"

    @property
    def signed_amount(self) -> float:
        return self.amount if self.direction == "in" else -self.amount

    @property
    def type_label(self) -> str:
        from shared.i18n import enum_label

        english = LEDGER_DOC_TYPE_LABELS.get(self.doc_type, self.doc_type)
        label = enum_label("doc_type", self.doc_type)
        return english if label == self.doc_type else label


LEDGER_AUDIT_ACTIONS = ("created", "edited", "cleared", "endorsed", "reopened", "deleted")


@dataclass
class LedgerAuditRecord:
    """One row of the ledger's append-only audit trail: who did what to
    entry `entry_id`, when (`at`, a UTC db timestamp), and the entry's
    fields (a dict) before and after - `before` is None for a creation,
    `after` None for a deletion."""

    id: int
    entry_id: int
    at: str
    actor_badge: str
    actor_name: str
    action: str
    before: dict | None = None
    after: dict | None = None

    @property
    def actor_label(self) -> str:
        return f"{self.actor_name} · {self.actor_badge}"


# --- Shipments / Distribution --------------------------------------------
#
# Match database/schema.sql's shipments CHECK constraint exactly.
SHIPMENT_STATUSES = ("scheduled", "in_transit", "delivered", "cancelled")


@dataclass
class ShipmentLine:
    product_barcode: str
    product_name: str
    expected_qty: int
    received_qty: int | None = None
    id: int | None = None

    @property
    def discrepancy(self) -> int:
        """received - expected once received (negative = short), else 0."""
        return 0 if self.received_qty is None else self.received_qty - self.expected_qty


@dataclass
class Shipment:
    """Goods going from a warehouse (`origin`) to a dealership.

    `status` is "scheduled", "in_transit", "delivered" or "cancelled";
    whether it's "Arriving" or "Delayed" is derived from `eta` /
    `planned_eta` vs. now by shared/distribution.py, never stored.
    Timestamps are the database's UTC ISO strings.
    """

    origin: str
    dealership_code: str
    dealership_name: str
    carrier: str
    planned_eta: str
    eta: str
    lines: list[ShipmentLine] = field(default_factory=list)
    driver: str | None = None
    status: str = "scheduled"
    departed_at: str | None = None
    delivered_at: str | None = None
    receipt_note: str | None = None
    id: int | None = None
    created_at: str | None = None
    origin_code: str | None = None  # the warehouse whose stock it comes out of (None: unassigned)
    stock_moved: bool = False  # the goods have left the origin's stock (dispatched)

    @property
    def number(self) -> str:
        return f"SH-{self.id:05d}" if self.id is not None else "SH-(new)"

    @property
    def item_count(self) -> int:
        return sum(line.expected_qty for line in self.lines)

    @property
    def is_active(self) -> bool:
        return self.status in ("scheduled", "in_transit")

    @property
    def discrepancies(self) -> list[ShipmentLine]:
        return [line for line in self.lines if line.discrepancy != 0]


# --- Dealership stock requests ---------------------------------------------

STOCK_REQUEST_STATUSES = ("open", "planned", "declined", "cancelled")


@dataclass
class StockRequest:
    """A dealership asking the depot for a product (POS > My Local Stock).

    `status`: "open" (waiting for the depot), "planned" (a shipment was
    planned from it - `shipment_id`), "declined" (by the depot, with
    `decision_note`) or "cancelled" (withdrawn by the dealership). Whether
    a planned one has arrived is the shipment's own status, joined in as
    `shipment_status`. Timestamps are the database's UTC ISO strings."""

    dealership_code: str
    dealership_name: str
    product_barcode: str
    product_name: str
    quantity: int
    status: str = "open"
    note: str | None = None
    requested_by: str | None = None
    decided_by: str | None = None
    decision_note: str | None = None
    shipment_id: int | None = None
    shipment_status: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def number(self) -> str:
        return f"RQ-{self.id:05d}" if self.id is not None else "RQ-(new)"

    @property
    def shipment_number(self) -> str | None:
        return f"SH-{self.shipment_id:05d}" if self.shipment_id is not None else None

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def delivered(self) -> bool:
        return self.status == "planned" and self.shipment_status == "delivered"


@dataclass(frozen=True)
class DealershipShortage:
    """A product at/below its reorder level on a dealership's shelf, with
    what's already on its way (`incoming_qty`, active shipments) and asked
    for (`requested_qty`, open requests) - Admin's and the depot's "low at
    dealerships" lists."""

    dealership_code: str
    dealership_name: str
    product_barcode: str
    product_name: str
    on_hand: int
    reorder_level: int
    incoming_qty: int = 0
    requested_qty: int = 0

    @property
    def covered(self) -> bool:
        """Enough is already coming or asked for to lift it above the reorder level."""
        return self.on_hand + self.incoming_qty + self.requested_qty > self.reorder_level

    @property
    def suggested_qty(self) -> int:
        return suggested_request_qty(self.on_hand, self.reorder_level, self.incoming_qty + self.requested_qty)


def suggested_request_qty(on_hand: int, reorder_level: int, already_coming: int = 0) -> int:
    """How much to ask for: back up to twice the reorder level, less what is
    already on its way. At least 1 when anything is wanted; 0 when nothing is."""
    if reorder_level <= 0:
        return 0
    return max(0, 2 * reorder_level - on_hand - already_coming)


# --- Warehouses & per-location stock ---------------------------------------

LOCATION_KINDS = ("warehouse", "dealership", "unassigned")


@dataclass(frozen=True)
class StockLocation:
    """A place stock can sit: a warehouse, a dealership (each by its
    code), or UNASSIGNED - stock not yet placed anywhere (what existed
    before per-location tracking; see database/migrations.py). Frozen so
    it can key a dict."""

    kind: str
    code: str = ""

    def __post_init__(self):
        if self.kind not in LOCATION_KINDS:
            raise ValueError(f"location kind must be one of {LOCATION_KINDS!r}, got {self.kind!r}")
        if self.kind == "unassigned" and self.code:
            raise ValueError("unassigned stock has no location code")
        if self.kind != "unassigned" and not self.code:
            raise ValueError(f"a {self.kind} location needs a code")

    @classmethod
    def warehouse(cls, code: str) -> "StockLocation":
        return cls("warehouse", code)

    @classmethod
    def dealership(cls, code: str) -> "StockLocation":
        return cls("dealership", code)

    @property
    def is_unassigned(self) -> bool:
        return self.kind == "unassigned"

    @property
    def label(self) -> str:
        """Short display text: the code, or "Unassigned"."""
        return "Unassigned" if self.is_unassigned else self.code


UNASSIGNED = StockLocation("unassigned")


@dataclass
class Warehouse:
    """A warehouse (admin_app's Warehouses page). Each depot_app instance
    runs one; `code` (e.g. "WH-01") is the natural key. `capacity_units`
    is None until set - see schema.sql. How full it is isn't a field: it's
    the live sum of its stock levels (stock_repository.units_by_location)."""

    code: str
    name: str
    city: str = ""
    capacity_units: int | None = None
    docks: int = 0
    is_active: bool = True
    id: int | None = None

    @property
    def site_label(self) -> str:
        """The "WH-01 · İstanbul Merkez" text the depot stamps on purchase
        orders, ledger documents and shipments (their `site`/`origin`)."""
        return f"{self.code} · {self.name}"

    @property
    def location(self) -> StockLocation:
        return StockLocation.warehouse(self.code)


# At or above this share of capacity a warehouse shows "Near capacity"
# (the Warehouses mockup's "85% threshold").
NEAR_CAPACITY_THRESHOLD = 0.85


@dataclass
class StockLevel:
    """How many units of one product sit at one location."""

    location: StockLocation
    product_barcode: str
    product_name: str
    quantity: int


@dataclass
class Account:
    """A sign-in account as Admin > Settings lists it: the employee's
    badge/name plus the account's role and state (see shared/auth.py).
    Never carries the PIN hash."""

    badge_id: str
    name: str
    role: str
    is_active: bool
    employee_active: bool
    location_type: str
    location_name: str
    failed_attempts: int = 0
    locked_until: str | None = None  # db timestamp; in the past = not locked
    last_sign_in_at: str | None = None
    id: int | None = None


@dataclass
class ReturnLine:
    """One product on a refund. `restock` False = damaged / not resellable."""

    product_barcode: str
    product_name_at_sale: str
    unit_price_at_sale: float
    quantity: int
    restock: bool = True

    @property
    def line_total(self) -> float:
        return round(self.unit_price_at_sale * self.quantity, 2)


@dataclass
class SaleReturn:
    """A refund against an earlier sale (database/sale_return_repository.py)."""

    transaction_id: int
    lines: list[ReturnLine] = field(default_factory=list)
    reason: str = ""
    id: int | None = None
    created_at: datetime | None = None
    dealership_code: str | None = None
    payment_method: str | None = None
    requested_by: str | None = None
    approved_by: str | None = None
    client_uuid: str | None = None

    @property
    def number(self) -> str:
        return "RF-%05d" % (self.id or 0)

    @property
    def total(self) -> float:
        return round(sum(line.line_total for line in self.lines), 2)


@dataclass(frozen=True)
class ReturnableLine:
    """What of one sold product can still come back."""

    product_barcode: str
    product_name_at_sale: str
    unit_price_at_sale: float
    sold: int
    returned: int

    @property
    def available(self) -> int:
        return self.sold - self.returned


@dataclass(frozen=True)
class DaySummary:
    """What one dealership's till should hold for one calendar day
    (database/day_close_repository.py)."""

    dealership_code: str | None
    business_date: date
    sales_count: int
    cash_sales: float
    card_sales: float
    other_sales: float  # sales whose payment method was never recorded
    cash_refunds: float
    card_refunds: float
    refunds_count: int

    @property
    def expected_cash(self) -> float:
        return round(self.cash_sales - self.cash_refunds, 2)

    @property
    def net_total(self) -> float:
        return round(self.cash_sales + self.card_sales + self.other_sales - self.cash_refunds - self.card_refunds, 2)


@dataclass
class DayClose:
    """One end-of-day count; difference = counted - expected (negative: cash is missing)."""

    dealership_code: str | None
    business_date: date
    sales_count: int
    cash_sales: float
    card_sales: float
    other_sales: float
    cash_refunds: float
    card_refunds: float
    expected_cash: float
    counted_cash: float
    difference: float
    note: str | None = None
    closed_by: str | None = None
    id: int | None = None
    created_at: datetime | None = None

