"""Server versions of the per-place stock functions in stock_repository
(transfers, counts, unplaced stock, reorder levels and the reads). Same
signatures and return shapes as the local ones; see remote_products.py for
products and stock in/out."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from database import remote
from database.exceptions import InsufficientStockError, ProductInactiveError, ProductNotFoundError
from shared.models import Product, StockLevel, StockLocation


def db_timestamp(iso: str | None) -> str | None:
    """Server time ('2026-11-01T10:00:00.123456+00:00') in the desktop's own
    stored format ('2026-11-01T10:00:00.123Z', UTC), so screens that compare or
    format created_at keep working."""
    if not iso:
        return None
    moment = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _q(**params) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return ("?" + urlencode(clean)) if clean else ""


def _place(location: StockLocation) -> dict:
    return {"kind": location.kind, "code": location.code}


def _loc(kind: str | None, code: str | None) -> StockLocation | None:
    return None if not kind else StockLocation(kind, code or "")


def _level(row: dict) -> StockLevel:
    return StockLevel(StockLocation(row["location_kind"], row["location_code"]), row["product_barcode"],
                      row.get("product_name") or row["product_barcode"], int(row["quantity"]))


def _not_enough(barcode: str, quantity: int, location: StockLocation):
    def build():
        have = quantity_at(location, barcode)
        return InsufficientStockError(str(barcode).strip().upper(), int(quantity), have, location.label)
    return build


# --- writes ----------------------------------------------------------------------

def transfer(source: StockLocation, destination: StockLocation, barcode: str, quantity: int,
             note: str | None) -> None:
    distribute(source, barcode, [(destination, quantity)], note)


def distribute(source: StockLocation, barcode: str, legs: list[tuple[StockLocation, int]], note: str | None) -> int:
    total = sum(q for _, q in legs)
    body = {"barcode": str(barcode).strip(), "source": _place(source), "note": note,
            "legs": [{**_place(loc), "quantity": int(q)} for loc, q in legs]}
    return int(remote.call("POST", "/stock/transfers", body,
                           errors={409: _not_enough(barcode, total, source)})["moved"])


def set_count(location: StockLocation, barcode: str, counted: int, note: str | None) -> int:
    body = {"barcode": str(barcode).strip(), "place": _place(location), "counted": int(counted), "note": note}
    return int(remote.call("POST", "/stock/counts", body)["difference"])


def clear_unassigned() -> int:
    return int(remote.call("POST", "/stock/unassigned/clear", {})["removed"])


def place_all_unassigned(destination: StockLocation) -> int:
    return int(remote.call("POST", "/stock/unassigned/place", _place(destination))["moved"])


def set_reorder_level(location: StockLocation, barcode: str, level: int | None) -> None:
    remote.call("PUT", "/stock/reorder-levels",
                {"place": _place(location), "barcode": str(barcode).strip(), "level": level},
                errors={404: lambda: ProductNotFoundError(barcode)})


# --- reads ---------------------------------------------------------------------

def reorder_overrides_at(location: StockLocation) -> dict[str, int]:
    return {k: int(v) for k, v in remote.call("GET", "/stock/reorder-levels" + _q(kind=location.kind,
                                                                                    code=location.code)).items()}


def products_at(location: StockLocation, include_inactive: bool = False) -> list[Product]:
    rows = remote.call("GET", "/stock/products-at" + _q(kind=location.kind, code=location.code,
                                                        include_inactive="true" if include_inactive else None))
    return [Product(barcode=r["barcode"], name=r["name"], price=float(r["price"]), stock_quantity=int(r["quantity"]),
                    critical_stock_level=int(r["critical_stock_level"]), is_active=bool(r["is_active"]),
                    stocked_here=bool(r["stocked_here"])) for r in rows]


def product_at(location: StockLocation, barcode: str, active_only: bool = False) -> Product:
    text = str(barcode).strip().upper()
    match = next((p for p in products_at(location, include_inactive=True) if p.barcode.upper() == text), None)
    if match is None:
        raise ProductNotFoundError(barcode)
    if active_only and not match.is_active:
        raise ProductInactiveError(match.barcode)
    return match


def quantity_at(location: StockLocation, barcode: str) -> int:
    rows = remote.call("GET", "/stock-levels" + _q(location_kind=location.kind, location_code=location.code,
                                                   product_barcode=str(barcode).strip()))
    return sum(int(r["quantity"]) for r in rows)


def _levels(**filters) -> list[StockLevel]:
    return [_level(r) for r in remote.call("GET", "/stock-levels" + _q(**filters)) if int(r["quantity"]) > 0]


def levels_for_product(barcode: str) -> list[StockLevel]:
    levels = _levels(product_barcode=str(barcode).strip())
    return sorted(levels, key=lambda lv: (-lv.quantity, lv.location.code))


def levels_at(location: StockLocation) -> list[StockLevel]:
    return sorted(_levels(location_kind=location.kind, location_code=location.code), key=lambda lv: lv.product_name)


def all_levels() -> list[StockLevel]:
    return sorted(_levels(), key=lambda lv: (lv.product_name, lv.location.kind, lv.location.code))


def units_by_location() -> dict[StockLocation, int]:
    return {StockLocation(r["location_kind"], r["location_code"]): int(r["units"])
            for r in remote.call("GET", "/stock/units-by-location")}


def units_in_transit() -> int:
    return int(remote.call("GET", "/stock/in-transit")["units"])


def list_movements(limit: int | None, location: StockLocation | None, movement_type: str | None,
                   location_kind: str | None, since: str | None, until: str | None) -> list[dict]:
    if location is not None and location_kind is not None and location.kind != location_kind:
        return []  # both filters given and they disagree: nothing matches (as in SQL)
    query = _q(location_kind=location.kind if location is not None else location_kind,
               location_code=location.code if location is not None else None,
               movement_type=movement_type, since=since, until=until, limit=5000 if limit is None else int(limit))
    rows = remote.call("GET", "/stock-movements" + query)
    return [{
        "id": r["id"], "barcode": r["barcode"], "product_name": r.get("product_name") or r["barcode"],
        "movement_type": r["direction"], "quantity": int(r["quantity"]), "note": r["note"],
        "created_at": db_timestamp(r["created_at"]), "location": _loc(r["location_kind"], r["location_code"]),
        "reason": r.get("reason"), "reference": r["reference"], "handled_by": r["handled_by"],
        "bin_code": r.get("bin_code"),
    } for r in rows]


def movement_totals_since(since_db_timestamp: str) -> dict[StockLocation, tuple[int, int]]:
    rows = remote.call("GET", "/stock/movement-totals" + _q(since=since_db_timestamp))
    return {StockLocation(r["location_kind"], r["location_code"]): (int(r["units_in"]), int(r["units_out"]))
            for r in rows}


def last_movement_at(location: StockLocation) -> dict[str, str]:
    rows = remote.call("GET", "/stock/last-moved" + _q(kind=location.kind, code=location.code))
    return {k: db_timestamp(v) for k, v in rows.items()}
