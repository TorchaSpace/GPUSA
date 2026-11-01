"""Server versions of the shipment calls in shipment_repository (plan, dispatch, re-time, cancel, receive,
the lists). Same signatures and return shapes as the local ones; the stock rules live on the server."""

from __future__ import annotations

from urllib.parse import urlencode

from database import remote
from database.exceptions import ShipmentNotFoundError
from database.remote_stock import db_timestamp
from shared.formatting import to_db_timestamp
from shared.models import Shipment, ShipmentLine


def _shipment(data: dict) -> Shipment:
    return Shipment(
        id=int(data["id"]), origin=data["origin"], dealership_code=data["dealership_code"],
        dealership_name=data["dealership_name"], carrier=data["carrier"], driver=data.get("driver"),
        status=data["status"], departed_at=db_timestamp(data.get("departed_at")),
        planned_eta=db_timestamp(data["planned_eta"]), eta=db_timestamp(data["eta"]),
        delivered_at=db_timestamp(data.get("delivered_at")), receipt_note=data.get("receipt_note"),
        created_at=db_timestamp(data.get("created_at")), origin_code=data.get("origin_code"),
        stock_moved=bool(data.get("stock_moved")),
        lines=[ShipmentLine(id=int(l["id"]), product_barcode=l["product_barcode"], product_name=l["product_name"],
                            expected_qty=int(l["expected_qty"]),
                            received_qty=None if l.get("received_qty") is None else int(l["received_qty"]))
               for l in data.get("lines", [])])


def _missing(shipment_id: int) -> dict:
    return {404: lambda: ShipmentNotFoundError(shipment_id)}


def create(origin: str, dealership_code: str, carrier: str, eta, lines: dict[str, int], driver: str | None,
           origin_code: str | None, departure, request_ids: list[int] | None) -> Shipment:
    body = {"origin": origin, "dealership_code": dealership_code, "carrier": carrier, "eta": to_db_timestamp(eta),
            "driver": driver, "origin_code": origin_code,
            "departure": to_db_timestamp(departure) if departure is not None else None,
            "lines": [{"barcode": b, "quantity": q} for b, q in lines.items()],
            "request_ids": list(request_ids or [])}
    return _shipment(remote.call("POST", "/shipments", body))


def get(shipment_id: int) -> Shipment:
    return _shipment(remote.call("GET", remote.path("shipments", shipment_id), errors=_missing(shipment_id)))


def list_shipments(statuses: tuple[str, ...] | None, dealership_code: str | None, origin: str | None,
                   origin_code: str | None) -> list[Shipment]:
    params = [("status", s) for s in (statuses or ())]
    params += [(k, v) for k, v in (("dealership_code", dealership_code), ("origin", origin),
                                   ("origin_code", origin_code)) if v is not None]
    return [_shipment(s) for s in remote.call("GET", "/shipments" + ("?" + urlencode(params) if params else ""))]


def count_incoming(dealership_code: str | None) -> int:
    query = "?" + urlencode({"dealership_code": dealership_code}) if dealership_code is not None else ""
    return int(remote.call("GET", "/shipments/incoming-count" + query)["count"])


def dispatch(shipment_id: int) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "dispatch"),
                                 errors=_missing(shipment_id)))


def update_eta(shipment_id: int, eta) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "eta"), {"eta": to_db_timestamp(eta)},
                                 errors=_missing(shipment_id)))


def cancel(shipment_id: int) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "cancel"),
                                 errors=_missing(shipment_id)))


def complete_receipt(shipment_id: int, received: dict[str, int], note: str | None) -> Shipment:
    return _shipment(remote.call("POST", remote.path("shipments", shipment_id, "receive"),
                                 {"received": received, "note": note}, errors=_missing(shipment_id)))
