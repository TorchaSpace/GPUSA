"""Where the signed-in person works: the shop a cashier's till opens at, the depot a manager's Floor shows.

A till or depot PC set up by the installer knows its place from its setup file. One started without it
(a second PC, a run from source) has nothing to go by, so it follows the person: their employee record
names the dealership or warehouse they work at. Head-office people (root, Admin accounts) have no such
place and get None; the caller then shows the first one and lets them move freely."""

from __future__ import annotations

from database import dealership_repository, employee_repository, warehouse_repository
from shared.auth import Session
from shared.models import Dealership, Warehouse
from shared.staff import StaffPlace

DEALERSHIP = "Dealership"
WAREHOUSE = "Warehouse"


def _employee(session: Session | None):
    if session is None:
        return None
    try:
        return employee_repository.get_by_badge_id(session.badge_id)
    except Exception:  # not an employee here, or the data cannot be read: no place, never a crash
        return None


def own_dealership(session: Session | None) -> Dealership | None:
    employee = _employee(session)
    if employee is None or employee.location_type != DEALERSHIP:
        return None
    try:
        shops = [d for d in dealership_repository.list_all() if d.is_active]
    except Exception:
        return None
    for shop in shops:
        if StaffPlace(DEALERSHIP, shop.code, shop.name).has(employee.location_type, employee.location_name):
            return shop
    return None


def own_warehouse(session: Session | None) -> Warehouse | None:
    employee = _employee(session)
    if employee is None or employee.location_type != WAREHOUSE:
        return None
    try:
        warehouses = warehouse_repository.list_all(active_only=True)
    except Exception:
        return None
    for warehouse in warehouses:
        if StaffPlace(WAREHOUSE, warehouse.code, warehouse.name).has(employee.location_type, employee.location_name):
            return warehouse
    return None


def moves_freely(session: Session | None) -> bool:
    """Administrators (root included) may look at every shop and depot without asking anyone."""
    return session is not None and session.role == "admin"
