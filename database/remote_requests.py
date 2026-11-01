"""Server versions of the dealership stock-request calls in stock_request_repository (ask, withdraw,
decline, the lists). Same signatures and return shapes as the local ones. Planning a shipment from
requests and the shortage view are shipment-side and stay local for now."""

from __future__ import annotations

from urllib.parse import urlencode

from database import remote
from database.exceptions import (
    DealershipInactiveError,
    DealershipNotFoundError,
    ProductInactiveError,
    ProductNotFoundError,
    StockRequestNotFoundError,
)
from database.remote_stock import db_timestamp
from shared.models import StockRequest

_PATH = "/stock-requests"


def _request(data: dict) -> StockRequest:
    return StockRequest(
        id=int(data["id"]), created_at=db_timestamp(data["created_at"]), updated_at=db_timestamp(data["updated_at"]),
        dealership_code=data["dealership_code"], dealership_name=data["dealership_name"],
        product_barcode=data["product_barcode"], product_name=data["product_name"], quantity=int(data["quantity"]),
        note=data.get("note"), status=data["status"], requested_by=data.get("requested_by"),
        decided_by=data.get("decided_by"), decision_note=data.get("decision_note"),
        shipment_id=data.get("shipment_id"), shipment_status=data.get("shipment_status"))


def create(dealership_code: str, barcode: str, quantity: int, note: str | None) -> StockRequest:
    return _request(remote.call("POST", _PATH, {
        "dealership_code": dealership_code, "barcode": barcode, "quantity": quantity, "note": note,
    }))


def get(request_id: int) -> StockRequest:
    return _request(remote.call("GET", remote.path("stock-requests", request_id),
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))


def list_requests(statuses: tuple[str, ...] | None, dealership_code: str | None,
                  limit: int | None) -> list[StockRequest]:
    params = [("status", s) for s in (statuses or ())]
    if dealership_code is not None:
        params.append(("dealership_code", dealership_code))
    if limit is not None:
        params.append(("limit", int(limit)))
    query = "?" + urlencode(params) if params else ""
    return [_request(r) for r in remote.call("GET", _PATH + query)]


def cancel(request_id: int) -> StockRequest:
    return _request(remote.call("POST", remote.path("stock-requests", request_id, "cancel"),
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))


def decline(request_id: int, note: str | None) -> StockRequest:
    return _request(remote.call("POST", remote.path("stock-requests", request_id, "decline"), {"note": note},
                                errors={404: lambda: StockRequestNotFoundError(request_id)}))
