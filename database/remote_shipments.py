"""Server versions of the shipment and stock-request repository functions.

shipment_repository and stock_request_repository call these instead of SQLite
when database.remote is active. Same signatures and return types as the local
functions; the rules (stock never below zero, one receipt per shipment, one open
request per dealership and product, who may do what) are checked on the server and
its refusals come back as ServerRefusedError with the server's own words."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from database import remote
from database.exceptions import (
    InsufficientStockError,
    ShipmentNotFoundError,
    StockRequestNotFoundError,
)
from database.remote_stock import db_timestamp
from shared.auth import Actor
from shared.formatting import to_db_timestamp
from shared.i18n import UserError
from shared.models import DealershipShortage, Shipment, ShipmentLine, StockLocation, StockRequest
from shared.warehousing import whole_number

MAX_NOTE_LENGTH = 200
MAX_REQUEST_QUANTITY = 100_000


def _q(**params) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    return ("?" + urlencode(clean)) if clean else ""


def _shipment(data: dict) -> Shipment:
    return Shipment(
        id=int(data["id"]), origin=data["origin"], origin_code=data.get("origin_code"),
        dealership_code=data["dealership_code"], dealership_name=data["dealership_name"],
        carrier=data["carrier"], driver=data.get("driver"), status=data["status"],
        departed_at=db_timestamp(data.get("departed_at")), planned_eta=db_timestamp(data["planned_eta"]),
        eta=db_timestamp(data["eta"]), delivered_at=db_timestamp(data.get("delivered_at")),
        receipt_note=data.get("receipt_note"), created_at=db_timestamp(data.get("created_at")),
        stock_moved=bool(data.get("stock_moved")),
        lines=[ShipmentLine(id=int(l["id"]), product_barcode=l["product_barcode"], product_name=l["product_name"],
                            expected_qty=int(l["expected_qty"]),
                            received_qty=None if l.get("received_qty") is None else int(l["received_qty"]))
               for l in data.get("lines", [])])


def _request(data: dict) -> StockRequest:
    return StockRequest(
        id=int(data["id"]), created_at=db_timestamp(data.get("created_at")),
        updated_at=db_timestamp(data.get("updated_at")), dealership_code=data["dealership_code"],
        dealership_name=data["dealership_name"], product_barcode=data["product_barcode"],
        product_name=data["product_name"], quantity=int(data["quantity"]), note=data.get("note"),
        status=data["status"], requested_by=data.get("requested_by"), decided_by=data.get("decided_by"),
        decision_note=data.get("decision_note"),
        shipment_id=None if data.get("shipment_id") is None else int(data["shipment_id"]),
        shipment_status=data.get("shipment_status"))


# --- shipments -------------------------------------------------------------------------

def get(shipment_id: int) -> Shipment:
    return _shipment(remote.call("GET", remote.path("shipments", shipment_id),
                                 errors={404: lambda: ShipmentNotFoundError(shipment_id)}))


def list_shipments(statuses=None, dealership_code=None, origin=None, origin_code=None) -> list[Shipment]:
    query = _q(status_in=",".join(statuses) if statuses else None, dealership_code=dealership_code,
               origin=origin, origin_code=origin_code)
    return [_shipment(row) for row in remote.call("GET", "/shipments" + query)]


def count_incoming(dealership_code: str | None) -> int:
    return int(remote.call("GET", "/shipments/incoming-count" + _q(dealership_code=dealership_code))["count"])


def create(origin, dealership_code, carrier, eta, lines, driver=None, origin_code=None, departure=None,
           request_ids=None, actor: Actor | None = None) -> Shipment:
    origin, carrier = (origin or "").strip(), (carrier or "").strip()
    if not origin:
        raise UserError("err.shipment_origin")
    if not carrier:
        raise UserError("err.carrier_required")
    body_lines: list[dict] = []
    for barcode, quantity in lines:
        quantity = whole_number(quantity, "Every line's quantity")
        if quantity <= 0:
            raise UserError("err.line_qty")
        body_lines.append({"barcode": str(barcode).strip(), "quantity": quantity})
    if not body_lines:
        raise UserError("err.add_product")
    body = {"origin": origin, "origin_code": (origin_code or "").strip() or None,
            "dealership_code": dealership_code, "carrier": carrier, "driver": (driver or "").strip() or None,
            "eta": to_db_timestamp(eta), "departure": to_db_timestamp(departure) if departure is not None else None,
            "lines": body_lines, "request_ids": [int(i) for i in (request_ids or [])]}
    return _shipment(remote.call("POST", "/shipments", body))


_SHORT = re.compile(r"Only (\d+) units of (\S+) at (.+?); (\d+) cannot leave")


def _not_enough(shipment_id: int):
    """A 409 on dispatch is either 'not scheduled any more' or 'the warehouse is short': the
    second one becomes the InsufficientStockError the Floor already knows how to word."""
    def build():
        return remote.ServerRefusedError("This shipment can't be dispatched.", 409)

    return build


def _dispatch_error(exc: remote.ServerRefusedError):
    found = _SHORT.search(str(exc))
    if found:
        return InsufficientStockError(found.group(2), int(found.group(4)), int(found.group(1)), found.group(3))
    return exc


def dispatch(shipment_id: int, actor: Actor | None = None) -> Shipment:
    try:
        return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "dispatch"), {},
                                     errors={404: lambda: ShipmentNotFoundError(shipment_id)}))
    except remote.ServerRefusedError as exc:
        raise _dispatch_error(exc) from None


def update_eta(shipment_id: int, eta) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "eta"), {"eta": to_db_timestamp(eta)},
                                 errors={404: lambda: ShipmentNotFoundError(shipment_id)}))


def cancel(shipment_id: int, actor: Actor | None = None) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "cancel"), {},
                                 errors={404: lambda: ShipmentNotFoundError(shipment_id)}))


def complete_receipt(shipment_id: int, received: dict, note: str | None = None,
                     actor: Actor | None = None) -> Shipment:
    counts: dict[str, int] = {}
    for barcode, quantity in received.items():
        quantity = whole_number(quantity, "Received quantities")
        if quantity < 0:
            raise UserError("err.received_negative")
        counts[str(barcode).strip()] = quantity
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "receive"),
                                 {"received": counts, "note": (note or "").strip() or None},
                                 errors={404: lambda: ShipmentNotFoundError(shipment_id)}))


# --- stock requests --------------------------------------------------------------------

def _clean_note(note: str | None) -> str | None:
    note = " ".join((note or "").split()) or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise UserError("err.stock_request_note_long", n=MAX_NOTE_LENGTH)
    return note


def request_create(dealership_code: str, barcode: str, quantity, note: str | None = None,
                   actor: Actor | None = None) -> StockRequest:
    quantity = whole_number(quantity, "The quantity")
    if quantity <= 0 or quantity > MAX_REQUEST_QUANTITY:
        raise UserError("err.stock_request_qty", n=MAX_REQUEST_QUANTITY)
    body = {"dealership_code": dealership_code, "barcode": str(barcode).strip(), "quantity": quantity,
            "note": _clean_note(note)}
    return _request(remote.call("POST", "/stock-requests", body))


def request_get(request_id: int) -> StockRequest:
    return _request(remote.call("GET", remote.path("stock-requests", request_id),
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))


def request_list(statuses=None, dealership_code=None, limit=None) -> list[StockRequest]:
    query = _q(status_in=",".join(statuses) if statuses else None, dealership_code=dealership_code,
               limit=int(limit) if limit is not None else None)
    return [_request(row) for row in remote.call("GET", "/stock-requests" + query)]


def request_count_open(dealership_code: str | None = None) -> int:
    return int(remote.call("GET", "/stock-requests/open-count" + _q(dealership_code=dealership_code))["count"])


def request_cancel(request_id: int, actor: Actor | None = None) -> StockRequest:
    return _request(remote.call("POST", remote.path("stock-requests", request_id, "cancel"), {},
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))


def request_decline(request_id: int, note: str | None = None, actor: Actor | None = None) -> StockRequest:
    return _request(remote.call("POST", remote.path("stock-requests", request_id, "decline"),
                                {"note": _clean_note(note)},
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))


def dealership_shortages(dealership_code: str | None = None) -> list[DealershipShortage]:
    """Same answer as the local one, built from what the server already lists: active dealerships'
    shelves at/below their reorder level, with units on active shipments and in open requests."""
    from database import dealership_repository, stock_repository

    shops = [d for d in dealership_repository.list_all() if d.is_active
             and (dealership_code is None or d.code == dealership_code)]
    incoming: dict[tuple[str, str], int] = {}
    for shipment in list_shipments(("scheduled", "in_transit")):
        for line in shipment.lines:
            key = (shipment.dealership_code, line.product_barcode.upper())
            incoming[key] = incoming.get(key, 0) + line.expected_qty
    requested: dict[tuple[str, str], int] = {}
    for req in request_list(("open",)):
        key = (req.dealership_code, req.product_barcode.upper())
        requested[key] = requested.get(key, 0) + req.quantity
    result: list[DealershipShortage] = []
    for shop in sorted(shops, key=lambda d: d.name):
        for product in stock_repository.critical_at(StockLocation.dealership(shop.code)):
            key = (shop.code, product.barcode.upper())
            result.append(DealershipShortage(
                dealership_code=shop.code, dealership_name=shop.name, product_barcode=product.barcode,
                product_name=product.name, on_hand=product.stock_quantity, reorder_level=product.critical_stock_level,
                incoming_qty=incoming.get(key, 0), requested_qty=requested.get(key, 0)))
    result.sort(key=lambda s: (s.covered, s.on_hand - s.reorder_level, s.dealership_name, s.product_name))
    return result
