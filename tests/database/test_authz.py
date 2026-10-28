"""A stale Admin window keeps no power: sensitive writes re-check, inside their
own transaction, that the actor is STILL an active administrator."""

from __future__ import annotations

from datetime import date

import pytest

import database.connection as connection
from database import (
    account_repository as accounts,
    dealership_repository,
    employee_repository,
    ledger_repository,
    product_repository,
    purchase_order_repository,
    warehouse_repository,
)
from database.exceptions import SessionInvalidError
from shared.models import Dealership, Employee, LedgerEntry, PriceRange, Product, Warehouse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _entry(doc_no="C-1") -> LedgerEntry:
    return LedgerEntry(direction="in", doc_type="check", doc_no=doc_no, counterparty="X",
                       issue_date=date.today(), due_date=date.today(), amount=500)


def _demote(admin_session, badge):
    accounts.set_role(badge, "cashier", by=admin_session.actor)


def _switch_off(admin_session, badge):
    accounts.set_active(badge, False, by=admin_session.actor)


def _employee_off(admin_session, badge):
    employee_repository.update(
        Employee(badge, "Admin B", "Management", "Dealership", "HQ", is_active=False), by=admin_session.actor
    )


@pytest.fixture
def world():
    boss = accounts.create_first_admin("A-1", "Aylin Boss", "482913")
    employee_repository.create(Employee("B-2", "Admin B", "Management", "Dealership", "HQ"))
    accounts.create_account("B-2", "admin", "246813")
    employee_repository.create(Employee("M-3", "Depot Manager", "Operations", "Warehouse", "WH-01"))
    accounts.create_account("M-3", "depot_manager", "7351")
    stale = accounts.authenticate("B-2", "246813", "admin", "T2").actor
    manager = accounts.authenticate("M-3", "7351", "depot_console", "T3").actor
    product_repository.create(Product("P-1", "Widget", 10, 0, 0))
    dealership_repository.create(Dealership(code="D9", name="Shop", region="Metro", city="X"))
    warehouse_repository.create(Warehouse(code="WH-9", name="Annex"))
    entry = ledger_repository.create(_entry(), boss.actor)
    return {"boss": boss, "stale": stale, "manager": manager, "entry": entry}


STALE_WAYS = [_demote, _switch_off, _employee_off]


def _operations(world):
    entry = world["entry"]
    return {
        "ledger_mark_cleared": lambda a: ledger_repository.mark_cleared(entry.id, a),
        "ledger_mark_endorsed": lambda a: ledger_repository.mark_endorsed(entry.id, a),
        "ledger_reopen": lambda a: ledger_repository.reopen(entry.id, a),
        "ledger_update": lambda a: ledger_repository.update(
            LedgerEntry(**{**entry.__dict__, "counterparty": "Changed"}), a),
        "ledger_delete": lambda a: ledger_repository.delete(entry.id, a),
        "ledger_create": lambda a: ledger_repository.create(_entry("C-2"), a),
        "product_delete": lambda a: product_repository.delete("P-1", a),
        "product_update": lambda a: product_repository.update(Product("P-1", "Renamed", 11, 0, 0), a),
        "product_set_active": lambda a: product_repository.set_active("P-1", False, a),
        "product_set_cost": lambda a: product_repository.set_cost("P-1", 3.0, a),
        "dealership_delete": lambda a: dealership_repository.delete("D9", a),
        "warehouse_delete": lambda a: warehouse_repository.delete("WH-9", a),
        "employee_delete": lambda a: employee_repository.delete("M-3", a),
        "price_set": lambda a: purchase_order_repository.set_price_range(PriceRange("P-1", 1.0, 2.0), a),
        "price_delete": lambda a: purchase_order_repository.delete_price_range("P-1", a),
    }


def _snapshot():
    with connection.connection_scope() as conn:
        return {
            table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]
            for table in ("ledger_entries", "products", "dealerships", "warehouses", "employees",
                          "purchase_price_ranges", "admin_audit")
        }


OPS = [
    "ledger_mark_cleared", "ledger_mark_endorsed", "ledger_reopen", "ledger_update", "ledger_delete",
    "ledger_create", "product_delete", "product_update", "product_set_active", "product_set_cost",
    "dealership_delete", "warehouse_delete", "employee_delete", "price_set", "price_delete",
]


@pytest.mark.parametrize("way", STALE_WAYS, ids=lambda f: f.__name__)
@pytest.mark.parametrize("op", OPS)
def test_a_stale_administrator_is_refused_and_nothing_changes(world, way, op):
    way(world["boss"], "B-2")
    before = _snapshot()
    with pytest.raises(SessionInvalidError):
        _operations(world)[op](world["stale"])
    assert _snapshot() == before


def test_a_live_administrator_and_a_missing_actor_still_work(world):
    ops = _operations(world)
    ops["product_set_cost"](world["stale"])  # B-2 is still a live admin
    assert product_repository.get_by_barcode("P-1").cost_price == 3.0
    product_repository.set_cost("P-1", 4.0)  # by=None (setup scripts, tests) skips the check
    assert product_repository.get_by_barcode("P-1").cost_price == 4.0


def test_a_depot_manager_may_record_a_ledger_entry_but_not_settle_it(world):
    manager, entry = world["manager"], world["entry"]
    created = ledger_repository.create(_entry("C-9"), manager)
    assert created.id is not None
    with pytest.raises(SessionInvalidError):
        ledger_repository.mark_cleared(entry.id, manager)
    assert ledger_repository.get(entry.id).status == "pending"
