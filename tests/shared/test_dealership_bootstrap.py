"""Regression tests for shared.dealership_bootstrap - turning the setup
wizard's POS_N.dealership.json sidecars into dealership rows, from Admin's
startup (every sidecar in its folder) and from each POS's own startup."""

from __future__ import annotations

import json
import sys

import pytest

import database.connection as connection
from database import dealership_repository
from shared import dealership_bootstrap


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _run_as(monkeypatch, exe_path):
    """Simulate running as a frozen .exe at exe_path - the same detection
    shared/paths.py's _exe_adjacent_config_path() uses."""
    exe_path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    return exe_path


@pytest.fixture
def frozen_exe(tmp_path, monkeypatch):
    return _run_as(monkeypatch, tmp_path / "POS_1.exe")


def _write_sidecar(exe_path, data: dict) -> None:
    sidecar = dealership_bootstrap.sidecar_path_for(exe_path)
    sidecar.write_text(json.dumps(data), encoding="utf-8")


def _read_sidecar(exe_path) -> dict:
    return json.loads(dealership_bootstrap.sidecar_path_for(exe_path).read_text(encoding="utf-8"))


def _log_text(exe_path) -> str:
    return dealership_bootstrap.log_path_for(exe_path).read_text(encoding="utf-8")


# --- POS startup: register_pending_dealership() ---------------------------


def test_no_sidecar_file_is_a_silent_no_op(frozen_exe):
    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.list_all() == []
    assert not dealership_bootstrap.log_path_for(frozen_exe).exists()


def test_not_frozen_is_a_silent_no_op(tmp_path, monkeypatch):
    exe_path = tmp_path / "POS_1.exe"
    exe_path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _write_sidecar(exe_path, {"code": "POS-1", "name": "Test"})

    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.list_all() == []


def test_corrupt_json_is_logged_not_raised(frozen_exe):
    dealership_bootstrap.sidecar_path_for(frozen_exe).write_text("{not valid json", encoding="utf-8")

    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.list_all() == []
    assert "could not read/parse" in _log_text(frozen_exe)


def test_missing_code_is_logged_not_raised(frozen_exe):
    _write_sidecar(frozen_exe, {"name": "No Code Here"})

    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.list_all() == []
    assert "no 'code' field" in _log_text(frozen_exe)


def test_registers_a_new_dealership(frozen_exe):
    _write_sidecar(
        frozen_exe,
        {"code": "POS-1", "name": "Riverbend Machinery", "region": "Valley",
         "city": "Boise, ID", "manager_name": "J. Alvarez"},
    )

    dealership_bootstrap.register_pending_dealership()

    dealership = dealership_repository.get_by_code("POS-1")
    assert dealership.name == "Riverbend Machinery"
    assert dealership.region == "Valley"
    assert dealership.city == "Boise, ID"
    assert dealership.manager_name == "J. Alvarez"
    assert dealership.is_active is True
    assert "registered 'POS-1'" in _log_text(frozen_exe)
    assert str(connection.get_db_path()) in _log_text(frozen_exe)


def test_fills_placeholder_defaults_when_fields_omitted(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-2"})

    dealership_bootstrap.register_pending_dealership()

    dealership = dealership_repository.get_by_code("POS-2")
    assert dealership.name == "POS-2"
    assert dealership.region == "Metro"
    assert dealership.city == "Unspecified"
    assert dealership.manager_name is None


def test_invalid_region_falls_back_to_default(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-3", "region": "Nowhere"})

    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.get_by_code("POS-3").region == "Metro"


def test_marks_the_sidecar_applied_after_success(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-1", "name": "First Name"})

    dealership_bootstrap.register_pending_dealership()

    data = _read_sidecar(frozen_exe)
    assert data["applied_at"]
    assert data["name"] == "First Name"  # original details kept


def test_idempotent_on_repeat_launches(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-1", "name": "First Name"})
    dealership_bootstrap.register_pending_dealership()

    dealership_bootstrap.register_pending_dealership()  # second launch

    assert len(dealership_repository.list_all()) == 1
    lines = _log_text(frozen_exe).splitlines()
    assert len(lines) == 2  # one line per launch - proof each launch ran
    assert "already applied" in lines[-1]


def test_does_not_overwrite_an_admin_edit_made_after_setup(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-1", "name": "From Setup"})
    dealership_bootstrap.register_pending_dealership()

    dealership = dealership_repository.get_by_code("POS-1")
    dealership.name = "Renamed By Admin"
    dealership_repository.update(dealership)

    dealership_bootstrap.register_pending_dealership()  # relaunch

    assert dealership_repository.get_by_code("POS-1").name == "Renamed By Admin"


def test_a_fresh_setup_run_updates_an_existing_code(frozen_exe):
    # The exact scenario from the bug report: code 001 was registered by
    # an earlier setup run as "North"; setup is re-run with new details
    # for 001. The new details must win - previously they were ignored.
    dealership_repository.create(
        dealership_bootstrap_models().Dealership(
            code="001", name="North", region="Valley", city="Isparta",
            manager_name="Fuat", is_active=True,
        )
    )
    _write_sidecar(
        frozen_exe,
        {"code": "001", "name": "321", "region": "Coastal", "city": "312", "manager_name": "1231"},
    )

    dealership_bootstrap.register_pending_dealership()

    dealership = dealership_repository.get_by_code("001")
    assert (dealership.name, dealership.region, dealership.city, dealership.manager_name) == (
        "321", "Coastal", "312", "1231",
    )
    assert len(dealership_repository.list_all()) == 1
    assert "name was 'North', now '321'" in _log_text(frozen_exe)


def test_reactivates_an_inactive_dealership_on_a_fresh_setup_run(frozen_exe):
    dealership_repository.create(
        dealership_bootstrap_models().Dealership(
            code="001", name="Old", region="Valley", city="X", manager_name=None, is_active=False,
        )
    )
    _write_sidecar(frozen_exe, {"code": "001", "name": "Old"})

    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.get_by_code("001").is_active is True


def test_unreachable_database_is_logged_and_not_marked_applied(frozen_exe, monkeypatch):
    _write_sidecar(frozen_exe, {"code": "POS-1"})

    def _boom(_dealership):
        raise OSError("disk full")

    monkeypatch.setattr("database.dealership_repository.create", _boom)

    dealership_bootstrap.register_pending_dealership()  # must not raise

    log_text = _log_text(frozen_exe)
    assert "FAILED to register 'POS-1'" in log_text
    assert "disk full" in log_text
    assert "applied_at" not in _read_sidecar(frozen_exe)  # retried next launch


def test_get_db_path_failure_does_not_raise(frozen_exe, monkeypatch):
    _write_sidecar(frozen_exe, {"code": "POS-1"})

    def _boom():
        raise OSError("disk full")

    monkeypatch.setattr(connection, "get_db_path", _boom)

    dealership_bootstrap.register_pending_dealership()  # must not raise
    assert "FAILED" in _log_text(frozen_exe)


# --- Admin startup: register_sidecars_beside_this_exe() --------------------


def test_admin_startup_registers_every_pos_sidecar_in_its_folder(tmp_path, monkeypatch):
    # The setup wizard's output folder: Admin_1.exe beside POS_1/POS_2 and
    # their sidecars. Opening Admin alone must be enough - no POS launched.
    _write_sidecar(tmp_path / "POS_1.exe", {"code": "001", "name": "321", "region": "Coastal"})
    _write_sidecar(tmp_path / "POS_2.exe", {"code": "002", "name": "43", "region": "Coastal"})
    admin_exe = _run_as(monkeypatch, tmp_path / "Admin_1.exe")

    dealership_bootstrap.register_sidecars_beside_this_exe()

    codes = sorted(d.code for d in dealership_repository.list_all() if d.is_active)
    assert codes == ["001", "002"]
    assert "Admin_1.exe: registered '001'" in _log_text(tmp_path / "POS_1.exe")
    assert "Admin_1.exe: registered '002'" in _log_text(tmp_path / "POS_2.exe")
    assert admin_exe.exists()


def test_pos_launch_after_admin_already_applied_is_a_no_op(tmp_path, monkeypatch):
    _write_sidecar(tmp_path / "POS_1.exe", {"code": "001", "name": "From Setup"})
    _run_as(monkeypatch, tmp_path / "Admin_1.exe")
    dealership_bootstrap.register_sidecars_beside_this_exe()

    dealership = dealership_repository.get_by_code("001")
    dealership.name = "Edited In Admin"
    dealership_repository.update(dealership)

    _run_as(monkeypatch, tmp_path / "POS_1.exe")
    dealership_bootstrap.register_pending_dealership()

    assert dealership_repository.get_by_code("001").name == "Edited In Admin"
    assert "POS_1.exe: nothing to do" in _log_text(tmp_path / "POS_1.exe")


def test_admin_startup_with_no_sidecars_is_a_no_op(tmp_path, monkeypatch):
    _run_as(monkeypatch, tmp_path / "Admin_1.exe")

    dealership_bootstrap.register_sidecars_beside_this_exe()

    assert dealership_repository.list_all() == []


def test_admin_startup_not_frozen_is_a_no_op(tmp_path, monkeypatch):
    _write_sidecar(tmp_path / "POS_1.exe", {"code": "001"})
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Admin_1.exe"))

    dealership_bootstrap.register_sidecars_beside_this_exe()

    assert dealership_repository.list_all() == []


def test_admin_startup_one_bad_sidecar_does_not_block_the_others(tmp_path, monkeypatch):
    (tmp_path / "POS_1.dealership.json").write_text("{broken", encoding="utf-8")
    _write_sidecar(tmp_path / "POS_2.exe", {"code": "002"})
    _run_as(monkeypatch, tmp_path / "Admin_1.exe")

    dealership_bootstrap.register_sidecars_beside_this_exe()

    assert [d.code for d in dealership_repository.list_all()] == ["002"]


# --- load_dealership_identity() (display-only, no database involved) -----


def test_load_identity_returns_none_without_sidecar(frozen_exe):
    assert dealership_bootstrap.load_dealership_identity() is None


def test_load_identity_returns_none_when_not_frozen(tmp_path, monkeypatch):
    exe_path = tmp_path / "POS_1.exe"
    exe_path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _write_sidecar(exe_path, {"code": "POS-1", "name": "Test"})

    assert dealership_bootstrap.load_dealership_identity() is None


def test_load_identity_returns_none_on_corrupt_json(frozen_exe):
    dealership_bootstrap.sidecar_path_for(frozen_exe).write_text("{not valid json", encoding="utf-8")

    assert dealership_bootstrap.load_dealership_identity() is None


def test_load_identity_reads_name_and_location(frozen_exe):
    _write_sidecar(
        frozen_exe,
        {"code": "CST-09", "name": "Harbor Point Equipment", "region": "Coastal", "city": "Norfolk, VA"},
    )

    assert dealership_bootstrap.load_dealership_identity() == {
        "code": "CST-09",
        "name": "Harbor Point Equipment",
        "location_line": "Norfolk, VA · Coastal",
    }


def test_load_identity_still_works_after_the_sidecar_is_marked_applied(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "CST-09", "name": "Harbor Point", "city": "Norfolk"})
    dealership_bootstrap.register_pending_dealership()

    assert dealership_bootstrap.load_dealership_identity()["name"] == "Harbor Point"


def test_load_identity_falls_back_to_code_for_missing_name(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-1"})

    identity = dealership_bootstrap.load_dealership_identity()

    assert identity["name"] == "POS-1"
    assert identity["location_line"] == "Unspecified location"


def test_load_identity_does_not_touch_the_database(frozen_exe):
    _write_sidecar(frozen_exe, {"code": "POS-1", "name": "Never Registered"})

    dealership_bootstrap.load_dealership_identity()

    assert dealership_repository.list_all() == []


def dealership_bootstrap_models():
    from shared import models

    return models
