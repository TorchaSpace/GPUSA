"""Server versions of the warehouse and dealership functions (reads, create, edit, delete), the product
delete, the store settings and the admin audit trail.

The rules (duplicate codes, "still holds stock", active shipments, open purchase orders, capacity below usage)
are checked on the server; its refusals come back as the exceptions the screens already catch."""

from __future__ import annotations

import re

from database import remote
from database.exceptions import (
    CapacityBelowUsageError,
    DealershipNotFoundError,
    DuplicateDealershipCodeError,
    DuplicateWarehouseCodeError,
    LocationHasOpenOrdersError,
    LocationHasStockError,
    LocationInUseError,
    ProductInUseError,
    ProductNotFoundError,
    SessionInvalidError,
    WarehouseNotFoundError,
)
from shared.models import Dealership, Warehouse


def _call(method: str, url: str, body: dict | None = None, *, kind: str, code: str):
    """kind: dealership, warehouse or product."""
    try:
        return remote.call(method, url, body)
    except remote.ServerRefusedError as exc:
        text = str(exc)
        if exc.status == 404:
            raise {"dealership": DealershipNotFoundError, "warehouse": WarehouseNotFoundError,
                   "product": ProductNotFoundError}[kind](code) from None
        if exc.status == 409:
            if re.search(r"(Dealership|Warehouse) \S+ already exists", text):
                raise (DuplicateDealershipCodeError if kind == "dealership" else DuplicateWarehouseCodeError)(code) from None
            if m := re.search(r"(\S+) still holds (\d+) units", text):
                raise LocationHasStockError(m.group(1), int(m.group(2))) from None
            if m := re.search(r"(?:Dealership|Warehouse) (\S+) has (\d+) active shipments", text):
                raise LocationInUseError(kind, m.group(1), int(m.group(2))) from None
            if m := re.search(r"Warehouse (\S+) has (\d+) open purchase orders", text):
                raise LocationHasOpenOrdersError(m.group(1), int(m.group(2))) from None
            if m := re.search(r"Warehouse (\S+) holds (\d+) units, over the capacity of (\d+)", text):
                raise CapacityBelowUsageError(m.group(1), int(m.group(3)), int(m.group(2))) from None
            if m := re.search(r"(\S+) can't be deleted: (.+?)\. Deactivate it instead", text):
                raise ProductInUseError(m.group(1), m.group(2)) from None
        raise


def warehouses(active_only: bool = False) -> list[Warehouse]:
    rows = remote.call("GET", "/warehouses")
    out = [Warehouse(code=r["code"], name=r["name"], city=r["city"], capacity_units=r["capacity_units"],
                     docks=int(r["docks"]), is_active=bool(r["is_active"]), id=r["id"]) for r in rows]
    return [w for w in out if w.is_active or not active_only]


def warehouse(code: str) -> Warehouse:
    text = (code or "").strip().upper()
    match = next((w for w in warehouses() if w.code.upper() == text), None)
    if match is None:
        raise WarehouseNotFoundError(code)
    return match


def dealerships() -> list[Dealership]:
    rows = remote.call("GET", "/dealerships")
    out = [Dealership(code=r["code"], name=r["name"], region=r["region"], city=r["city"],
                      manager_name=r["manager_name"], is_active=bool(r["is_active"]), id=r["id"]) for r in rows]
    return sorted(out, key=lambda d: d.name)


def dealership(code: str, normalized: str) -> Dealership:
    match = next((d for d in dealerships() if d.code.upper() == normalized.upper()), None)
    if match is None:
        raise DealershipNotFoundError(normalized)
    return match


# --- writes ---------------------------------------------------------------------------

def create_dealership(d: Dealership) -> None:
    _call("POST", "/dealerships", {"code": d.code, "name": d.name, "region": d.region, "city": d.city,
                                   "manager_name": d.manager_name, "is_active": bool(d.is_active)},
          kind="dealership", code=d.code)


def update_dealership(d: Dealership) -> None:
    _call("PUT", f"/dealerships/{d.code.strip()}", {"code": d.code, "name": d.name, "region": d.region, "city": d.city,
                                                    "manager_name": d.manager_name, "is_active": bool(d.is_active)},
          kind="dealership", code=d.code)


def delete_dealership(code: str) -> None:
    _call("DELETE", f"/dealerships/{code.strip()}", kind="dealership", code=code)


def _wbody(w: Warehouse) -> dict:
    return {"code": w.code, "name": w.name, "city": w.city, "capacity_units": w.capacity_units,
            "docks": w.docks, "is_active": bool(w.is_active)}


def create_warehouse(w: Warehouse) -> Warehouse:
    data = _call("POST", "/warehouses", _wbody(w), kind="warehouse", code=w.code)
    w.id = data["id"]
    w.code = data["code"]
    return w


def update_warehouse(w: Warehouse) -> None:
    _call("PUT", f"/warehouses/{w.code.strip()}", _wbody(w), kind="warehouse", code=w.code)


def delete_warehouse(code: str) -> None:
    _call("DELETE", f"/warehouses/{code.strip()}", kind="warehouse", code=code)


def delete_product(barcode: str) -> None:
    _call("DELETE", f"/products/{barcode.strip()}", kind="product", code=barcode)


# --- settings and audit -----------------------------------------------------------------

def settings_all() -> dict[str, str]:
    values = dict(remote.call("GET", "/settings"))
    try:
        values.update(remote.call("GET", "/settings/private"))
    except (remote.ServerRefusedError, SessionInvalidError):  # not an administrator: no read-cursors
        pass
    return values


def settings_save(values: dict[str, str]) -> None:
    remote.call("PUT", "/settings", {"values": dict(values)})


def audit_recent(limit: int, kind: str | None):
    from urllib.parse import urlencode

    from database.audit_repository import AuditEntry
    from database.remote_stock import db_timestamp

    query = {"limit": max(0, int(limit))}
    if kind is not None:
        query["kind"] = kind
    return [AuditEntry(id=int(r["id"]), at=db_timestamp(r["at"]), actor=r.get("actor"), action=r["action"],
                       kind=r["kind"], target=r["target"], detail=r.get("detail"))
            for r in remote.call("GET", "/audit?" + urlencode(query))]
