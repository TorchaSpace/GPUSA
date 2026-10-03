"""What the Admin Overview page shows, computed from plain data.

Pure functions - no Qt, no database - same split as shared/analytics.py.
Overview used to show "not tracked yet" for its revenue, warehouse and
dealership cards because none of that existed; it all does now (sales,
warehouses with capacity and per-location stock, dealerships, shipments),
so these turn those records into the three cards and the stock-by-location
grid the mockup draws.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.models import Dealership, Product, StockLevel, StockLocation, Warehouse
from shared.warehousing import STATUS_INACTIVE, STATUS_NEAR, capacity_fraction, capacity_status

FILTER_ALL, FILTER_WAREHOUSES, FILTER_DEALERSHIPS = "all", "warehouses", "dealerships"
FILTERS = (FILTER_ALL, FILTER_WAREHOUSES, FILTER_DEALERSHIPS)

STATUS_OUT, STATUS_LOW, STATUS_IN = "Out of stock", "Low stock", "In stock"


@dataclass(frozen=True)
class WarehouseUse:
    code: str
    name: str
    used: int
    capacity: int | None
    fraction: float | None
    status: str


@dataclass(frozen=True)
class CapacitySummary:
    active: int
    inactive: int
    overall: float | None  # share of the combined set capacity in use; None when no capacity is set anywhere
    warehouses: list[WarehouseUse]
    near: list[str]  # codes at/above the 85% threshold


def capacity_summary(warehouses: list[Warehouse], used: dict[StockLocation, int]) -> CapacitySummary:
    active = [w for w in warehouses if w.is_active]
    rows = [
        WarehouseUse(
            w.code, w.name, used.get(w.location, 0), w.capacity_units,
            capacity_fraction(used.get(w.location, 0), w.capacity_units), capacity_status(w, used.get(w.location, 0)),
        )
        for w in active
    ]
    with_capacity = [r for r in rows if r.capacity]
    total_capacity = sum(r.capacity for r in with_capacity)
    overall = (sum(r.used for r in with_capacity) / total_capacity) if total_capacity else None
    return CapacitySummary(
        active=len(active), inactive=len(warehouses) - len(active), overall=overall, warehouses=rows,
        near=[r.code for r in rows if r.status == STATUS_NEAR],
    )


@dataclass(frozen=True)
class DealershipSummary:
    active: int
    inactive: int
    selling: int  # active dealerships with at least one sale in the window
    silent: int  # active dealerships with none
    by_region: dict[str, int] = field(default_factory=dict)


def dealership_summary(dealerships: list[Dealership], selling_codes: set[str]) -> DealershipSummary:
    active = [d for d in dealerships if d.is_active]
    regions: dict[str, int] = {}
    for d in active:
        regions[d.region] = regions.get(d.region, 0) + 1
    selling = sum(1 for d in active if d.code in selling_codes)
    return DealershipSummary(len(active), len(dealerships) - len(active), selling, len(active) - selling, regions)


# --- Stock by location ----------------------------------------------------


@dataclass(frozen=True)
class StockRow:
    barcode: str
    name: str
    cells: list[int]  # one per column of the grid
    total: int  # over the visible columns
    network_total: int  # the product's company-wide stock
    reorder_at: int
    status: str  # judged on the whole network, whatever columns are shown


@dataclass(frozen=True)
class StockGrid:
    columns: list[str]
    rows: list[StockRow]


def product_status(product: Product) -> str:
    if product.stock_quantity <= 0:
        return STATUS_OUT
    return STATUS_LOW if product.is_below_critical_stock else STATUS_IN


def stock_grid(
    products: list[Product],
    levels: list[StockLevel],
    warehouses: list[Warehouse],
    location_filter: str = FILTER_ALL,
    below_reorder_only: bool = False,
    query: str = "",
) -> StockGrid:
    """One row per product, one column per active warehouse, then
    "Dealerships" (all of them added together - the mockup's per-region
    columns would need a region on every stock level), "Unassigned" and,
    when any units are on a truck, "On the road".

    The Warehouses / Dealerships filter keeps only those columns and
    totals just them. Status is always judged on the whole network (the
    reorder level is a company-wide number), so a product doesn't look
    healthy merely because the visible columns hide where it ran out."""
    if location_filter not in FILTERS:
        raise ValueError(f"filter must be one of {FILTERS!r}, got {location_filter!r}")
    active = [w for w in warehouses if w.is_active]
    held: dict[tuple[str, str], int] = {}  # (barcode, column key) -> units
    for level in levels:
        kind = level.location.kind
        key = level.location.code if kind == "warehouse" else kind
        held[(level.product_barcode, key)] = held.get((level.product_barcode, key), 0) + level.quantity

    codes = {w.code for w in active}
    # Units at a warehouse that is now inactive or deleted still exist; fold
    # them into "Unassigned" rather than dropping them from the totals.
    for (barcode, key), units in list(held.items()):
        if key not in codes and key not in ("dealership", "unassigned"):
            del held[(barcode, key)]
            held[(barcode, "unassigned")] = held.get((barcode, "unassigned"), 0) + units

    def on_the_road(product: Product) -> int:
        in_places = sum(units for (barcode, _), units in held.items() if barcode == product.barcode)
        return max(0, product.stock_quantity - in_places)

    road_any = any(on_the_road(p) for p in products)
    keys: list[tuple[str, str]] = []  # (column key, header)
    if location_filter in (FILTER_ALL, FILTER_WAREHOUSES):
        keys += [(w.code, w.code) for w in active]
    if location_filter in (FILTER_ALL, FILTER_DEALERSHIPS):
        keys.append(("dealership", "Dealerships"))
    if location_filter == FILTER_ALL:
        keys.append(("unassigned", "Unassigned"))
        if road_any:
            keys.append(("road", "On the road"))

    needle = query.strip().lower()
    rows: list[StockRow] = []
    for product in products:
        status = product_status(product)
        if below_reorder_only and status == STATUS_IN:
            continue
        if needle and needle not in f"{product.barcode} {product.name}".lower():
            continue
        cells = [
            on_the_road(product) if key == "road" else held.get((product.barcode, key), 0) for key, _ in keys
        ]
        rows.append(
            StockRow(product.barcode, product.name, cells, sum(cells), product.stock_quantity,
                     product.critical_stock_level, status)
        )
    return StockGrid([header for _, header in keys], rows)


@dataclass(frozen=True)
class AttendanceToday:
    on_site: int
    checked_out: int
    not_in: int  # active employees with no check-in today
    rows: list[dict]  # today's roster entries that have a check-in, newest first


def attendance_today(roster: list[dict]) -> AttendanceToday:
    """Summarise attendance_repository.list_roster() for the Overview's
    employee log. Inactive employees are ignored; "not in" is a plain fact
    (no check-in recorded today) - there is no shift schedule to call
    someone late or absent against."""
    active = [e for e in roster if e.get("is_active", True)]
    seen = [e for e in active if e["status"] != "Off"]
    seen.sort(key=lambda e: e.get("check_in_at") or "", reverse=True)
    return AttendanceToday(
        on_site=sum(1 for e in active if e["status"] == "Present"),
        checked_out=sum(1 for e in active if e["status"] == "Checked out"),
        not_in=sum(1 for e in active if e["status"] == "Off"),
        rows=seen,
    )


__all__ = [
    "AttendanceToday", "CapacitySummary", "attendance_today", "DealershipSummary", "FILTERS", "FILTER_ALL", "FILTER_DEALERSHIPS", "FILTER_WAREHOUSES",
    "STATUS_INACTIVE", "STATUS_IN", "STATUS_LOW", "STATUS_OUT", "StockGrid", "StockRow", "WarehouseUse",
    "capacity_summary", "dealership_summary", "product_status", "stock_grid",
]
