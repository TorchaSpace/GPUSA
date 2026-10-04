"""Floor movements keep the reference and bin in their own columns and can be
credited to a checked-in operator; migration v6 adds the bin column."""

from __future__ import annotations

import sqlite3

import pytest

import database.connection as connection
from database import attendance_repository, employee_repository, migrations, product_repository, stock_repository
from database.exceptions import EmployeeInactiveError, EmployeeNotFoundError, NoOpenAttendanceRecordError
from shared.models import UNASSIGNED, Employee, Product
from shared.warehousing import reference_text


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_reference_and_bin_are_stored_separately():
    product_repository.create(Product("BOX", "Carton", 5, 0, 1))
    stock_repository.receive(UNASSIGNED, "BOX", 10, reference=" PO-1 ", bin_code="A-03")
    stock_repository.dispatch(UNASSIGNED, "BOX", 4, reference="SO-9", bin_code=None)
    out, inn = stock_repository.list_movements(limit=2)
    assert (inn["reference"], inn["bin_code"], inn["note"]) == ("PO-1", "A-03", None)
    assert (out["reference"], out["bin_code"]) == ("SO-9", None)
    assert reference_text(inn).startswith("PO-1 · A-03")


def test_operator_must_be_checked_in_and_active():
    employee_repository.create(Employee("B-1", "Elena Varga", "Operations", "Warehouse", "Depot"))
    employee_repository.create(Employee("B-2", "Gone", "Operations", "Warehouse", "Depot", is_active=False))
    with pytest.raises(EmployeeNotFoundError):
        attendance_repository.operator_actor("NOPE")
    with pytest.raises(EmployeeInactiveError):
        attendance_repository.operator_actor("B-2")
    with pytest.raises(NoOpenAttendanceRecordError):
        attendance_repository.operator_actor("B-1")
    attendance_repository.check_in("B-1")
    actor = attendance_repository.operator_actor("b-1")
    assert actor.label == "Elena Varga · B-1"
    product_repository.create(Product("BOX", "Carton", 5, 0, 1))
    stock_repository.receive(UNASSIGNED, "BOX", 3, actor=actor)
    assert stock_repository.list_movements(limit=1)[0]["handled_by"] == "Elena Varga · B-1"


def test_migration_v6_adds_the_bin_column(tmp_path):
    old = sqlite3.connect(tmp_path / "t.db")
    old.executescript(
        "CREATE TABLE stock_movements (id INTEGER PRIMARY KEY, product_barcode TEXT, movement_type TEXT, "
        "quantity INTEGER, note TEXT, created_at TEXT, location_kind TEXT, location_code TEXT, reason TEXT, "
        "reference TEXT, handled_by TEXT);"
        "PRAGMA user_version = 5;"
    )
    old.close()
    conn = sqlite3.connect(tmp_path / "t.db")
    migrations._to_v6(conn)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(stock_movements)")]
    assert "bin_code" in columns
    migrations._to_v6(conn)  # running it twice changes nothing
    conn.close()
