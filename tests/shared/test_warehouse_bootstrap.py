"""shared.warehouse_bootstrap - Depot_N.warehouse.json sidecars from the
setup wizards becoming warehouse rows, and which warehouse a depot is."""

from __future__ import annotations

import json
import sys

import pytest

import database.connection as connection
from database import warehouse_repository
from shared import warehouse_bootstrap as wb
from shared.models import Warehouse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _run_as(monkeypatch, exe_path):
    exe_path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    return exe_path


def _sidecar(exe_path, data):
    path = wb.warehouse_sidecar_path_for(exe_path)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


DETAILS = {"code": "WH-01", "name": "İstanbul Merkez", "city": "Tuzla", "capacity_units": 5400, "docks": 8}


def test_depot_registers_its_own_warehouse_once(tmp_path, monkeypatch):
    exe = _run_as(monkeypatch, tmp_path / "Depot_1.exe")
    path = _sidecar(exe, DETAILS)

    wb.register_pending_warehouse()
    w = warehouse_repository.get_by_code("WH-01")
    assert (w.name, w.city, w.capacity_units, w.docks, w.is_active) == ("İstanbul Merkez", "Tuzla", 5400, 8, True)
    assert json.loads(path.read_text(encoding="utf-8"))["applied_at"]

    w.name = "Renamed in Admin"
    warehouse_repository.update(w)
    wb.register_pending_warehouse()  # stamped: Admin's edit is kept
    assert warehouse_repository.get_by_code("WH-01").name == "Renamed in Admin"
    assert "already applied" in (tmp_path / "Depot_1.warehouse.log").read_text(encoding="utf-8")


def test_admin_applies_every_depot_sidecar_in_its_folder(tmp_path, monkeypatch):
    _sidecar(tmp_path / "Depot_1.exe", DETAILS)
    _sidecar(tmp_path / "Depot_2.exe", {"code": "WH-02", "name": "Gebze", "capacity_units": "", "docks": "x"})
    _run_as(monkeypatch, tmp_path / "Admin_1.exe")

    wb.register_warehouse_sidecars_beside_this_exe()

    assert [w.code for w in warehouse_repository.list_all()] == ["WH-01", "WH-02"]
    second = warehouse_repository.get_by_code("WH-02")
    assert (second.capacity_units, second.docks) == (None, 0)  # bad values fall back, don't fail


def test_fresh_setup_updates_an_existing_code(tmp_path, monkeypatch):
    warehouse_repository.create(Warehouse(code="WH-01", name="Old", is_active=False))
    exe = _run_as(monkeypatch, tmp_path / "Depot_1.exe")
    _sidecar(exe, DETAILS)
    wb.register_pending_warehouse()
    w = warehouse_repository.get_by_code("WH-01")
    assert (w.name, w.is_active) == ("İstanbul Merkez", True)


def test_bad_sidecars_are_logged_not_raised(tmp_path, monkeypatch):
    exe = _run_as(monkeypatch, tmp_path / "Depot_1.exe")
    wb.warehouse_sidecar_path_for(exe).write_text("{not json", encoding="utf-8")
    wb.register_pending_warehouse()
    _sidecar(exe, {"name": "no code"})
    wb.register_pending_warehouse()
    log = (tmp_path / "Depot_1.warehouse.log").read_text(encoding="utf-8")
    assert "could not read" in log and "no 'code'" in log
    assert warehouse_repository.list_all() == []


def test_resolve_uses_the_sidecar_identity(tmp_path, monkeypatch):
    exe = _run_as(monkeypatch, tmp_path / "Depot_3.exe")
    _sidecar(exe, DETAILS)
    assert wb.load_warehouse_identity().site_label == "WH-01 · İstanbul Merkez"
    # Not applied yet (e.g. it failed earlier) - resolving creates it.
    assert wb.resolve_this_depot_warehouse().capacity_units == 5400
    assert warehouse_repository.get_by_code("WH-01").city == "Tuzla"


def test_resolve_without_identity_uses_the_default():
    assert wb.load_warehouse_identity() is None  # dev run
    assert wb.resolve_this_depot_warehouse().site_label == "WH-01 · Main warehouse"


def test_resolve_when_the_database_is_unreachable(monkeypatch):
    def broken():
        raise RuntimeError("no db")

    monkeypatch.setattr(warehouse_repository, "ensure_default", broken)
    assert wb.resolve_this_depot_warehouse().code == "WH-01"
