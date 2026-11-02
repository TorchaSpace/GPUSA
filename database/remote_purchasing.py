"""Server versions of the purchase-order and price-band repository functions.

purchase_order_repository calls these instead of SQLite when database.remote is active. Same signatures
and return types as the local functions. The rules (price bands decide sent vs held, only the first admin
decision counts, a delivery moves order + stock + cost in one step, who may cancel what) are checked on the
server; its refusals come back as the same exceptions the screens already catch, or with the server's own
words."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from database import remote
from database.exceptions import (
    ProductNotFoundError,
    PurchaseOrderAlreadyDecidedError,
    PurchaseOrderCancelNotAllowedError,
    PurchaseOrderNotFoundError,
    PurchaseOrderOverReceiveError,
    PurchaseOrderStateError,
)
from database.remote_stock import db_timestamp
from shared.models import PriceRange, PurchaseOrder, StockLocation


def _number_to_id(text: str) -> int:
    return int(text.split("-")[1])


def _call(method: str, url: str, body: dict | None = None, *, errors: dict | None = None):
    try:
        return remote.call(method, url, body, errors=errors)
    except remote.ServerRefusedError as exc:
        text = str(exc)
        if exc.status == 409:
            if m := re.search(r"(PO-\d+) was decided already: it is (\w+)\.", text):
                raise PurchaseOrderAlreadyDecidedError(m.group(1), m.group(2)) from None
            if m := re.search(r"(PO-\d+) awaits (\d+) more units; (\d+) were booked", text):
                raise PurchaseOrderOverReceiveError(m.group(1), int(m.group(3)), int(m.group(2))) from None
            if m := re.search(r"(PO-\d+) is (\w+); it cannot be (received|cancelled) now", text):
                raise PurchaseOrderStateError(m.group(1), m.group(2), m.group(3)) from None
        if exc.status == 403:
            if m := re.search(r"Only an administrator can cancel (PO-\d+) once it is (\w+)", text):
                raise PurchaseOrderCancelNotAllowedError(m.group(1), m.group(2), "admin_only") from None
            if m := re.search(r"(PO-\d+) was raised by someone else", text):
                raise PurchaseOrderCancelNotAllowedError(m.group(1), "pending", "not_yours") from None
        raise


def _order(data: dict) -> PurchaseOrder:
    return PurchaseOrder(
        id=int(data["id"]), product_barcode=data["product_barcode"], product_name=data["product_name"],
        supplier=data["supplier"], quantity=int(data["quantity"]), unit_price=float(data["unit_price"]),
        site=data["site"], status=data["status"], hold_reason=data.get("hold_reason"),
        range_min=data.get("range_min"), range_max=data.get("range_max"),
        created_at=db_timestamp(data.get("created_at")), decided_at=db_timestamp(data.get("decided_at")),
        decision_note=data.get("decision_note"), raised_by=data.get("raised_by"), decided_by=data.get("decided_by"),
        received_qty=int(data.get("received_qty") or 0), received_at=db_timestamp(data.get("received_at")),
        received_by=data.get("received_by"), cancelled_at=db_timestamp(data.get("cancelled_at")),
        cancelled_by=data.get("cancelled_by"))


# --- bands -------------------------------------------------------------------------------

def _band(data: dict) -> PriceRange:
    return PriceRange(product_barcode=data["product_barcode"], min_unit_price=float(data["min_unit_price"]),
                      max_unit_price=float(data["max_unit_price"]), default_supplier=data.get("default_supplier"))


def get_price_range(barcode: str) -> PriceRange | None:
    got = _call("GET", remote.path("price-ranges", barcode))
    return _band(got) if got else None


def list_price_ranges() -> list[PriceRange]:
    return [_band(r) for r in _call("GET", "/price-ranges")]


def set_price_range(price_range: PriceRange, low: float, high: float) -> None:
    barcode = price_range.product_barcode
    _call("PUT", remote.path("price-ranges", barcode),
          {"min_unit_price": low, "max_unit_price": high, "default_supplier": price_range.default_supplier},
          errors={404: lambda: ProductNotFoundError(barcode)})


def delete_price_range(barcode: str) -> None:
    _call("DELETE", remote.path("price-ranges", barcode))


# --- orders ------------------------------------------------------------------------------

def submit(barcode: str, supplier: str, quantity: int, unit_price: float, site: str) -> PurchaseOrder:
    return _order(_call("POST", "/purchase-orders", {
        "barcode": barcode, "supplier": supplier, "quantity": int(quantity), "unit_price": unit_price, "site": site},
        errors={404: lambda: ProductNotFoundError(barcode)}))


def get(order_id: int) -> PurchaseOrder:
    return _order(_call("GET", remote.path("purchase-orders", str(order_id)),
                        errors={404: lambda: PurchaseOrderNotFoundError(order_id)}))


def list_orders(status: str | None, site: str | None, limit: int | None) -> list[PurchaseOrder]:
    query = {k: v for k, v in (("status", status), ("site", site), ("limit", limit)) if v is not None}
    return [_order(r) for r in _call("GET", "/purchase-orders" + (("?" + urlencode(query)) if query else ""))]


def pending_ids() -> list[int]:
    return [int(i) for i in _call("GET", "/purchase-orders/pending-ids")]


def decide(order_id: int, verb: str, note: str | None) -> PurchaseOrder:
    return _order(_call("POST", remote.path("purchase-orders", str(order_id), verb), {"note": note},
                        errors={404: lambda: PurchaseOrderNotFoundError(order_id)}))


def receive_against_order(order_id: int, quantity: int, warehouse_code: str) -> PurchaseOrder:
    return _order(_call("POST", remote.path("purchase-orders", str(order_id), "receive"),
                        {"quantity": int(quantity), "warehouse_code": warehouse_code},
                        errors={404: lambda: PurchaseOrderNotFoundError(order_id)}))


def receive_delivery(location: StockLocation, barcode: str, quantity: int, note: str | None, reference: str | None,
                     bin_code: str | None) -> list[str]:
    got = _call("POST", "/purchase-orders/receive-delivery", {
        "warehouse_code": location.code, "barcode": barcode, "quantity": int(quantity), "note": note,
        "reference": reference, "bin_code": bin_code}, errors={404: lambda: ProductNotFoundError(barcode)})
    return list(got["orders"])


def cancel(order_id: int) -> PurchaseOrder:
    return _order(_call("POST", remote.path("purchase-orders", str(order_id), "cancel"), {},
                        errors={404: lambda: PurchaseOrderNotFoundError(order_id)}))
