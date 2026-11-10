"""Where the signed-in person works (database/own_place.py)."""

from __future__ import annotations

import pytest

from database import account_repository, dealership_repository, employee_repository, own_place, warehouse_repository
from shared import auth
from shared.models import Dealership, Employee, Warehouse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def people():
    account_repository.create_first_admin("A-1", "Erol", "482913")
    warehouse_repository.create(Warehouse(code="WH-01", name="Ana Depo"))
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze"))
    dealership_repository.create(Dealership(code="IZM-01", name="Alsancak Bayi", region="Coastal", city="İzmir"))
    dealership_repository.create(Dealership(code="IST-01", name="Kadıköy Bayi", region="Metro", city="İstanbul"))
    employee_repository.create(Employee("K-1", "Selin", "Sales & service", "Dealership", "kadıköy bayi"))
    account_repository.create_account("K-1", "cashier", "583102")
    employee_repository.create(Employee("D-1", "Deniz", "Management", "Warehouse", "WH-02"))  # by code
    account_repository.create_account("D-1", "depot_manager", "735102")


def _session(badge, pin, area):
    return account_repository.authenticate(badge, pin, area, "test")


def test_a_cashier_belongs_to_the_shop_in_their_employee_record(people):
    cashier = _session("K-1", "583102", auth.AREA_POS)
    assert own_place.own_dealership(cashier).code == "IST-01"
    assert own_place.own_warehouse(cashier) is None and not own_place.moves_freely(cashier)


def test_a_depot_manager_belongs_to_their_depot(people):
    manager = _session("D-1", "735102", auth.AREA_DEPOT_CONSOLE)
    assert own_place.own_warehouse(manager).code == "WH-02"
    assert own_place.own_dealership(manager) is None


def test_administrators_have_no_place_and_move_freely(people):
    admin = _session("A-1", "482913", auth.AREA_POS)
    assert own_place.own_dealership(admin) is None and own_place.own_warehouse(admin) is None
    assert own_place.moves_freely(admin)


def test_nobody_and_unreadable_data_give_no_place(people, monkeypatch):
    assert own_place.own_dealership(None) is None and not own_place.moves_freely(None)
    cashier = _session("K-1", "583102", auth.AREA_POS)

    def broken(*_a, **_k):
        raise RuntimeError("no data")

    monkeypatch.setattr(employee_repository, "get_by_badge_id", broken)
    assert own_place.own_dealership(cashier) is None


def test_a_switched_off_shop_is_nobodys_home(people):
    shop = dealership_repository.get_by_code("IST-01")
    shop.is_active = False
    dealership_repository.update(shop)
    assert own_place.own_dealership(_session("K-1", "583102", auth.AREA_POS)) is None
