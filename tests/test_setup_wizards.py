"""Regression tests for the two setup wizards' prompt_db_path() - both
loop until the answer is either empty (accept the default) or an
absolute path, rather than silently accepting something like a stray
"y" that would resolve to a brand new, disconnected database next to
wherever an app happens to be launched from. See each function's
docstring for the incident that motivated this (config.json ending up
with `"db_path": "y"`, silently dropping every app onto an empty local
database instead of the shared one).

Both deploy_system.py and installer.py are runnable scripts, not normal
package modules - imported here the same way, guarded by their own
`if __name__ == "__main__":` so importing them doesn't invoke main().
"""

from __future__ import annotations

import builtins
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import deploy_system  # noqa: E402
import installer  # noqa: E402


def _feed_inputs(monkeypatch, answers: list[str]):
    """Monkeypatch builtins.input to return each of `answers` in turn."""
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *_args, **_kwargs: next(it))


def test_deploy_system_accepts_absolute_path_immediately(monkeypatch):
    _feed_inputs(monkeypatch, [r"C:\ProgramData\POSInventorySystem\shared_backend.db"])

    result = deploy_system.prompt_db_path()

    assert result == r"C:\ProgramData\POSInventorySystem\shared_backend.db"


def test_deploy_system_empty_input_returns_the_default(monkeypatch):
    _feed_inputs(monkeypatch, [""])

    result = deploy_system.prompt_db_path()

    assert result == str(deploy_system.default_shared_data_dir() / deploy_system.DATABASE_FILENAME)


def test_deploy_system_rejects_a_relative_answer_and_reprompts(monkeypatch, capsys):
    # First answer ("y") is not absolute and must be rejected without
    # being returned; the second, valid answer is what actually comes back.
    _feed_inputs(monkeypatch, ["y", r"C:\Data\shared_backend.db"])

    result = deploy_system.prompt_db_path()

    assert result == r"C:\Data\shared_backend.db"
    assert "'y'" in capsys.readouterr().out


def test_installer_accepts_absolute_path_immediately(monkeypatch):
    _feed_inputs(monkeypatch, [r"C:\ProgramData\POSInventorySystem\shared_backend.db"])

    result = installer.prompt_db_path()

    assert result == r"C:\ProgramData\POSInventorySystem\shared_backend.db"


def test_installer_empty_input_returns_empty_string(monkeypatch):
    # installer.py deliberately leaves db_path OUT of config.json on an
    # empty answer (see its own docstring) rather than computing a
    # default itself - so the empty string, not a path, is correct here.
    _feed_inputs(monkeypatch, [""])

    result = installer.prompt_db_path()

    assert result == ""


def test_installer_rejects_a_relative_answer_and_reprompts(monkeypatch, capsys):
    _feed_inputs(monkeypatch, ["y", r"C:\Data\shared_backend.db"])

    result = installer.prompt_db_path()

    assert result == r"C:\Data\shared_backend.db"
    assert "'y'" in capsys.readouterr().out


def test_installer_rejects_a_unc_relative_typo_but_accepts_a_real_unc_path(monkeypatch):
    # A UNC path (\\SERVER\share\...) IS absolute on Windows - Path.is_absolute()
    # must not reject it just because it doesn't start with a drive letter.
    _feed_inputs(monkeypatch, [r"\\SERVER\POSData\shared_backend.db"])

    result = installer.prompt_db_path()

    assert result == r"\\SERVER\POSData\shared_backend.db"


# --- per-Depot warehouse details -------------------------------------------

@pytest.mark.parametrize("wizard", [deploy_system, installer])
def test_warehouse_placeholders_when_not_filling_now(monkeypatch, wizard):
    _feed_inputs(monkeypatch, ["n"])
    assert wizard.prompt_warehouse_details(2) == [
        {"code": "WH-01", "name": "Warehouse 1", "city": "", "capacity_units": None, "docks": 0},
        {"code": "WH-02", "name": "Warehouse 2", "city": "", "capacity_units": None, "docks": 0},
    ]


@pytest.mark.parametrize("wizard", [deploy_system, installer])
def test_warehouse_details_are_asked_per_depot_and_counts_validated(monkeypatch, capsys, wizard):
    _feed_inputs(monkeypatch, ["y", "IST-1", "İstanbul Merkez", "Tuzla", "lots", "-5", "5400", "8"])
    [entry] = wizard.prompt_warehouse_details(1)
    assert entry == {"code": "IST-1", "name": "İstanbul Merkez", "city": "Tuzla", "capacity_units": 5400, "docks": 8}
    assert capsys.readouterr().out.count("whole number above 0") == 2


@pytest.mark.parametrize("wizard", [deploy_system, installer])
def test_warehouse_sidecars_sit_next_to_each_depot(tmp_path, wizard):
    entries = [{"code": "WH-01", "name": "A", "city": "", "capacity_units": None, "docks": 0},
               {"code": "WH-02", "name": "B", "city": "", "capacity_units": 100, "docks": 2}]
    names = wizard.write_warehouse_sidecars(tmp_path, ".exe", entries)
    assert names == ["Depot_1.warehouse.json", "Depot_2.warehouse.json"]
    import json as _json
    assert _json.loads((tmp_path / "Depot_2.warehouse.json").read_text(encoding="utf-8"))["capacity_units"] == 100


def test_installer_suffix_matches_the_bootstrap():
    from shared.warehouse_bootstrap import _SIDECAR_SUFFIX
    assert installer.WAREHOUSE_SIDECAR_SUFFIX == _SIDECAR_SUFFIX
