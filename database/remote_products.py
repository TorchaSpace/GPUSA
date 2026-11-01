"""Server versions of the product and stock in/out repository functions.

product_repository and stock_repository call these instead of SQLite when
database.remote is active. Same signatures and return types as the local
functions; the checks (validation, duplicate barcodes, live role, stock
never below zero) happen on the server, and its refusals come back as the
local exception types.
"""

from __future__ import annotations

from database import remote
from database.exceptions import (
    DuplicateBarcodeError,
    InsufficientStockError,
    ProductInactiveError,
    ProductNotFoundError,
    UnknownLocationError,
)
from shared.models import Product, StockLevel, StockLocation


def _product(data: dict) -> Product:
    return Product(barcode=data["barcode"], name=data["name"], price=float(data["price"]),
                   stock_quantity=int(data["stock_quantity"]),
                   critical_stock_level=int(data["critical_stock_level"]), is_active=bool(data["is_active"]),
                   cost_price=float(data.get("cost_price") or 0))


def list_all(include_inactive: bool = True) -> list[Product]:
    rows = remote.call("GET", "/products?include_inactive=" + ("true" if include_inactive else "false"))
    products = [_product(r) for r in rows]
    if not include_inactive:  # a non-manager account gets active ones anyway; be explicit
        products = [p for p in products if p.is_active]
    return sorted(products, key=lambda p: p.name)


def get_by_barcode(barcode: str, active_only: bool = False) -> Product:
    text = str(barcode).strip()
    product = _product(remote.call("GET", remote.path("products", text),
                                   errors={404: lambda: ProductNotFoundError(barcode)}))
    if active_only and not product.is_active:
        raise ProductInactiveError(product.barcode)
    return product


def create(product: Product) -> None:
    remote.call("POST", "/products", {
        "barcode": product.barcode, "name": product.name, "price": product.price,
        "stock_quantity": product.stock_quantity, "critical_stock_level": product.critical_stock_level,
        "is_active": product.is_active, "cost_price": product.cost_price,
    }, errors={409: lambda: DuplicateBarcodeError(str(product.barcode).strip().upper())})


def _patch(barcode: str, body: dict) -> dict:
    return remote.call("PATCH", remote.path("products", str(barcode).strip()), body,
                       errors={404: lambda: ProductNotFoundError(barcode)})


def update(product: Product) -> None:
    _patch(product.barcode, {"name": product.name, "price": product.price,
                             "critical_stock_level": product.critical_stock_level})


def set_cost(barcode: str, cost: float) -> float:
    return float(_patch(barcode, {"cost_price": cost}).get("cost_price") or 0)


def set_active(barcode: str, active: bool) -> None:
    _patch(barcode, {"is_active": bool(active)})


def levels_for_product(barcode: str) -> list[StockLevel]:
    from database import remote_stock  # the per-place reads live there

    return remote_stock.levels_for_product(barcode)


def _move(direction: str, location: StockLocation, barcode: str, quantity: int, note: str | None,
          reference: str | None, bin_code: str | None) -> int:
    def not_enough() -> Exception:
        # For a dispatch the server's 409 means "not enough stock here". (A receipt's
        # 409 - switched-off product, full or switched-off warehouse - keeps the
        # server's own words as a ServerRefusedError.)
        have = next((lv.quantity for lv in levels_for_product(barcode) if lv.location == location), 0)
        return InsufficientStockError(str(barcode).strip().upper(), int(quantity), have, location.label)

    body = {"direction": direction, "barcode": str(barcode).strip(), "location_kind": location.kind,
            "location_code": location.code, "quantity": int(quantity), "note": note, "reference": reference,
            "bin_code": bin_code}
    errors = {404: lambda: _not_found(location, barcode)}
    if direction == "dispatch":
        errors[409] = not_enough
    return int(remote.call("POST", "/stock-movements", body, errors=errors)["level_after"])


def _not_found(location: StockLocation, barcode: str) -> Exception:
    """The server's 404 is either the place or the product; ask which."""
    try:
        get_by_barcode(barcode)
    except ProductNotFoundError as exc:
        return exc
    return UnknownLocationError(location.kind, location.code)


def receive(location: StockLocation, barcode: str, quantity: int, note: str | None = None,
            reference: str | None = None, bin_code: str | None = None) -> int:
    return _move("receive", location, barcode, quantity, note, reference, bin_code)


def dispatch(location: StockLocation, barcode: str, quantity: int, note: str | None = None,
             reference: str | None = None, bin_code: str | None = None) -> int:
    return _move("dispatch", location, barcode, quantity, note, reference, bin_code)
