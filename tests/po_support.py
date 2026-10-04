"""Shared helpers for the purchase-order tests: a warehouse, a product,
people with real accounts (cancel_order reads the actor's role from the
database) and an approved order ready to receive."""

from __future__ import annotations

from database import (
    account_repository,
    employee_repository,
    product_repository,
    purchase_order_repository as po_repo,
    warehouse_repository,
)
from shared.auth import Actor
from shared.models import PriceRange, Product, StockLocation, Warehouse

WH1 = StockLocation.warehouse("WH-01")
SITE = "WH-01 · İstanbul Merkez"

ADMIN = Actor("A-1", "Ada Admin")
DEPOT = Actor("D-1", "Deniz Depo")
OTHER_DEPOT = Actor("D-2", "Orhan Depo")
CASHIER = Actor("C-1", "Can Kasa")
STRANGER = Actor("X-9", "No Account")

_PEOPLE = (
    (ADMIN, "admin", "482913", "Management"),
    (DEPOT, "depot_manager", "5831", "Operations"),
    (OTHER_DEPOT, "depot_manager", "7294", "Operations"),
    (CASHIER, "cashier", "6417", "Sales & service"),
)


def seed_people() -> None:
    from shared.models import Employee

    for actor, role, pin, employee_role in _PEOPLE:
        employee_repository.create(Employee(actor.badge_id, actor.name, employee_role, "Warehouse", "WH-01"))
        account_repository.create_account(actor.badge_id, role, pin)


def seed_world(*, capacity: int | None = 1000, stock: int = 0, cost: float = 0.0) -> None:
    """WH-01, product PLT-4410 (price 900, `stock` units unassigned at `cost`), a safe band 520-680."""
    warehouse_repository.create(Warehouse(code="WH-01", name="İstanbul Merkez", city="Tuzla",
                                          capacity_units=capacity, docks=4))
    product_repository.create(Product("PLT-4410", "Pallet wrap 500mm", 900, stock, 10, cost_price=cost))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680, "Kuzey Ambalaj A.Ş."))


def sent_order(quantity: int = 100, price: float = 600.0, raised_by: Actor | None = DEPOT):
    """An order inside the band: placed with the supplier at once ('sent')."""
    return po_repo.submit("PLT-4410", "Kuzey Ambalaj A.Ş.", quantity, price, SITE, raised_by=raised_by)


def pending_order(quantity: int = 100, price: float = 742.5, raised_by: Actor | None = DEPOT):
    """An order above the band: held for an administrator."""
    return po_repo.submit("PLT-4410", "Kuzey Ambalaj A.Ş.", quantity, price, SITE, raised_by=raised_by)


def session_for(actor: Actor, role: str, area: str = "depot_console"):
    """A signed-in Session for the GUI (current_session.set(...)); the role
    must match the account seed_people() created for the actor."""
    from shared.auth import Session

    return Session(account_id=1, badge_id=actor.badge_id, name=actor.name, role=role,
                   signed_in_at="2026-09-24T09:00:00.000Z", area=area, terminal="Test")
