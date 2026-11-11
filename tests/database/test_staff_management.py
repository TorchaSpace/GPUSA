"""Depot / dealership staff: national IDs, a place managing only its own people, punches at the till,
the in/out history, and migration v12."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

import database.connection as connection
from database import account_repository, attendance_repository, dealership_repository, employee_repository, migrations
from database.exceptions import (
    DuplicateNationalIdError,
    EmployeeOtherPlaceError,
    EmployeeOtherSiteError,
    SessionInvalidError,
)
from shared.auth import AREA_DEPOT_CONSOLE, AREA_POS, Actor
from shared.models import Dealership, Employee
from shared.staff import DEALERSHIP, WAREHOUSE, StaffPlace

DEPOT = StaffPlace(WAREHOUSE, "WH-01", "Merkez")
SHOP = StaffPlace(DEALERSHIP, "CST-04", "Coast")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def cashier():
    dealership_repository.create(Dealership(code="CST-04", name="Coast", region="Coastal", city="Izmir"))
    employee_repository.create(Employee("C-1", "Cem", "Sales & service", "Dealership", "CST-04"))
    account_repository.create_account("C-1", "cashier", "583920")
    return Actor("C-1", "Cem")


def person(badge="P-1", place=SHOP, national_id="10000000146", **extra):
    return Employee(badge, "Ayşe Yılmaz", "Sales & service", place.kind, place.name, national_id=national_id, **extra)


def test_shop_adds_its_own_person_with_a_national_id(cashier):
    employee_repository.create(person(national_id="100 000 001 46"), place=SHOP, by=cashier, area=AREA_POS,
                               require_national_id=True)
    saved = employee_repository.get_by_badge_id("P-1")
    assert saved.national_id == "10000000146" and saved.location_name == "Coast"


def test_national_id_is_required_valid_and_unique(cashier):
    with pytest.raises(ValueError):
        employee_repository.create(person(national_id=None), place=SHOP, require_national_id=True)
    with pytest.raises(ValueError):
        employee_repository.create(person(national_id="10000000147"), place=SHOP)
    employee_repository.create(person(), place=SHOP)
    with pytest.raises(DuplicateNationalIdError):
        employee_repository.create(person("P-2"), place=SHOP)


def test_a_place_cannot_add_or_take_another_places_person(cashier):
    with pytest.raises(EmployeeOtherPlaceError):
        employee_repository.create(person(place=DEPOT), place=SHOP)
    employee_repository.create(Employee("W-1", "Veli", "Operations", "Warehouse", "Merkez"))
    with pytest.raises(EmployeeOtherPlaceError):  # the depot's person is not the shop's to edit
        employee_repository.update(Employee("W-1", "Veli", "Operations", "Dealership", "Coast"), place=SHOP)
    employee_repository.create(person(), place=SHOP)
    with pytest.raises(EmployeeOtherPlaceError):  # nor can the shop move its own away
        employee_repository.update(person(place=DEPOT, national_id=None), place=SHOP)


def test_edit_keeps_the_national_id_when_left_empty(cashier):
    employee_repository.create(person(), place=SHOP)
    employee_repository.update(person(national_id=None, title="Kasiyer", is_active=False), place=SHOP, by=cashier,
                               area=AREA_POS)
    saved = employee_repository.get_by_badge_id("P-1")
    assert (saved.national_id, saved.title, saved.is_active) == ("10000000146", "Kasiyer", False)


def test_the_signed_in_person_is_rechecked_for_the_app(cashier):
    with pytest.raises(SessionInvalidError):  # a cashier's account cannot open the depot console
        employee_repository.create(person(place=DEPOT), place=DEPOT, by=cashier, area=AREA_DEPOT_CONSOLE)


def test_till_punches_only_its_own_people(cashier):
    employee_repository.create(person(), place=SHOP)
    employee_repository.create(Employee("W-1", "Veli", "Operations", "Warehouse", "Merkez"))
    attendance_repository.check_in("p-1", dealership_code="CST-04")
    with pytest.raises(EmployeeOtherSiteError):
        attendance_repository.check_in("W-1", dealership_code="CST-04")
    attendance_repository.check_out("P-1", dealership_code="cst-04")


def test_history_lists_the_places_people_over_days(cashier):
    employee_repository.create(person(), place=SHOP)
    employee_repository.create(Employee("W-1", "Veli", "Operations", "Warehouse", "Merkez"))
    attendance_repository.check_in("P-1")
    attendance_repository.check_out("P-1")
    attendance_repository.check_in("W-1")
    with connection.connection_scope() as conn:  # yesterday's shift for P-1
        conn.execute("INSERT INTO attendance_records (employee_id, check_in_at, check_out_at) SELECT id, "
                     "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 day', '-8 hours'), "
                     "strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '-1 day') FROM employees WHERE badge_id = 'P-1'")
    today = date.today()
    shop_today = attendance_repository.list_history(today, today, place=SHOP)
    assert [r["badge_id"] for r in shop_today] == ["P-1"]
    both_days = attendance_repository.list_history(today - timedelta(days=2), today, place=SHOP)
    assert len(both_days) == 2 and both_days[0]["check_in_at"] > both_days[1]["check_in_at"]
    assert both_days[1]["minutes"] == 480
    everyone = attendance_repository.list_history(today, today)
    assert {r["badge_id"] for r in everyone} == {"P-1", "W-1"}
    assert [r["badge_id"] for r in attendance_repository.list_history(today, today, badge_id="w-1")] == ["W-1"]
    assert attendance_repository.list_history(today, today, place=DEPOT)[0]["open"] is True


def test_roster_carries_the_national_id(cashier):
    employee_repository.create(person(), place=SHOP)
    entry = next(r for r in attendance_repository.list_roster() if r["badge_id"] == "P-1")
    assert entry["national_id"] == "10000000146"


def test_next_badge_id_skips_taken_ones(cashier):
    employee_repository.create(Employee("CST-04-0002", "X", "Sales & service", "Dealership", "Coast"))
    assert employee_repository.next_badge_id("CST-04") == "CST-04-0003"


def test_migration_v12_adds_the_column_and_unique_index(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(connection._SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "    national_id   TEXT\n", "    placeholder   TEXT\n"))
    raw.execute("PRAGMA user_version = 11")
    raw.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                "VALUES ('OLD', 'Old', 'Operations', 'Warehouse', 'X')")
    raw.commit()
    migrations.run_migrations(raw)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == migrations.LATEST_VERSION
    assert raw.execute("SELECT national_id FROM employees").fetchone() == (None,)
    raw.execute("UPDATE employees SET national_id = '10000000146'")
    raw.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                "VALUES ('NEW', 'New', 'Operations', 'Warehouse', 'X')")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE employees SET national_id = '10000000146' WHERE badge_id = 'NEW'")
    raw.close()


def test_migration_v13_makes_the_oldest_active_admin_root(tmp_path):
    raw = sqlite3.connect(tmp_path / "old.db")
    raw.executescript(connection._SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "    is_root          INTEGER NOT NULL DEFAULT 0 CHECK (is_root IN (0, 1)),\n", ""))
    raw.execute("PRAGMA user_version = 12")
    for n, (badge, role) in enumerate([("A", "cashier"), ("B", "admin"), ("C", "admin")], start=1):
        raw.execute("INSERT INTO employees (badge_id, name, role, location_type, location_name) "
                    "VALUES (?, ?, 'Operations', 'Warehouse', 'X')", (badge, badge))
        raw.execute("INSERT INTO accounts (employee_id, role, pin_hash) VALUES (?, ?, 'x')", (n, role))
    raw.commit()
    migrations.run_migrations(raw)
    assert raw.execute("SELECT employee_id FROM accounts WHERE is_root = 1").fetchall() == [(2,)]


def test_the_shop_manager_gives_their_cashiers_a_till_pin(cashier):
    from database import activity_repository
    from database.exceptions import EmployeeNotFoundError
    from shared.i18n import UserError

    employee_repository.create(person(), place=SHOP)
    with pytest.raises(UserError):   # a plain cashier (job: Sales & service) gives nobody a PIN
        account_repository.create_till_account("P-1", "583920", cashier, SHOP)
    manager = employee_repository.get_by_badge_id("C-1")
    manager.role = "Management"
    employee_repository.update(manager)
    account = account_repository.create_till_account("p-1", "583920", cashier, SHOP)
    assert (account.badge_id, account.role) == ("P-1", "cashier")
    assert account_repository.authenticate("P-1", "583920", AREA_POS, "Till 1").role == "cashier"
    with pytest.raises(UserError):   # once is enough
        account_repository.create_till_account("P-1", "583920", cashier, SHOP)
    dealership_repository.create(Dealership(code="MTR-01", name="Metro", region="Metro", city="Ankara"))
    employee_repository.create(Employee("P-9", "Elsewhere", "Sales & service", "Dealership", "Metro"))
    with pytest.raises(UserError):   # not this shop's person
        account_repository.create_till_account("P-9", "583920", cashier, SHOP)
    with pytest.raises(EmployeeNotFoundError):
        account_repository.create_till_account("NOPE", "583920", cashier, SHOP)
    with pytest.raises(ValueError):  # a weak PIN
        account_repository.create_till_account("P-1", "1234", cashier, SHOP)
    assert [e.kind for e in activity_repository.list_since(0)].count("till_account_added") == 1
