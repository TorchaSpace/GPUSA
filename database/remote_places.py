"""Server versions of the warehouse and dealership reads. Creating, editing
and deleting places is not on the server yet (NotOnServerYetError)."""

from __future__ import annotations

from database import remote
from database.exceptions import DealershipNotFoundError, WarehouseNotFoundError
from shared.models import Dealership, Warehouse


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
