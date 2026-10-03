"""Turning the setup wizard's per-Depot warehouse details into real
`warehouses` rows - the warehouse twin of shared/dealership_bootstrap.py,
with the same rules (read that module's docstring for the why):

- The wizards (deploy_system.py, installer.py) write a
  "Depot_<n>.warehouse.json" sidecar next to each Depot instance, holding
  what was typed in during setup: code, name, city, capacity (units),
  docks.
- admin_app at startup applies every such sidecar in its own folder
  (register_warehouse_sidecars_beside_this_exe()), so the warehouses
  appear in Admin > Warehouses right after setup; depot_app at startup
  applies its own (register_pending_warehouse()) - the fallback for a
  Depot .exe copied to another machine.
- Latest setup wins, then hands off to Admin: an unstamped sidecar
  creates the warehouse, or updates an existing one with the same code,
  and gets "applied_at" stamped in; later launches leave the row alone so
  edits made in Admin are kept.
- Never fatal, never silent: outcomes go to "Depot_<n>.warehouse.log".

resolve_this_depot_warehouse() is what depot_app uses to know which
warehouse it IS: the one its sidecar names, or - with no sidecar (a dev
run, or an install from before warehouses existed) -
warehouse_repository.ensure_default().
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.dealership_bootstrap import _APPLIED_KEY, _log, _now_utc, _running_exe_path
from shared.models import Warehouse

_SIDECAR_SUFFIX = ".warehouse.json"
_LOG_SUFFIX = ".warehouse.log"


def warehouse_sidecar_path_for(exe_path: Path) -> Path:
    """Depot_2.exe -> Depot_2.warehouse.json. Used by both wizards."""
    return exe_path.with_name(exe_path.stem + _SIDECAR_SUFFIX)


def _log_path_for_sidecar(sidecar: Path) -> Path:
    name = sidecar.name
    stem = name[: -len(_SIDECAR_SUFFIX)] if name.endswith(_SIDECAR_SUFFIX) else sidecar.stem
    return sidecar.with_name(stem + _LOG_SUFFIX)


def _optional_positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _warehouse_from(data: dict) -> Warehouse | None:
    code = str(data.get("code") or "").strip()
    if not code:
        return None
    try:
        docks = max(0, int(data.get("docks") or 0))
    except (TypeError, ValueError):
        docks = 0
    return Warehouse(
        code=code,
        name=str(data.get("name") or code).strip() or code,
        city=str(data.get("city") or "").strip(),
        capacity_units=_optional_positive_int(data.get("capacity_units")),
        docks=docks,
        is_active=True,
    )


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_warehouse_identity() -> Warehouse | None:
    """This Depot instance's warehouse as its own sidecar describes it
    (no database access), or None in a dev run / with no usable sidecar."""
    exe_path = _running_exe_path()
    if exe_path is None:
        return None
    path = warehouse_sidecar_path_for(exe_path)
    if not path.exists():
        return None
    data = _read(path)
    return _warehouse_from(data) if data else None


def apply_warehouse_sidecar(sidecar: Path, applied_by: str) -> None:
    """Create/update the warehouse a sidecar describes, unless already
    applied. Never raises; logs every outcome next to the sidecar."""
    log_path = _log_path_for_sidecar(sidecar)
    data = _read(sidecar)
    if data is None:
        _log(log_path, f"{applied_by}: skipped - could not read/parse {sidecar.name}")
        return
    warehouse = _warehouse_from(data)
    if warehouse is None:
        _log(log_path, f"{applied_by}: skipped - {sidecar.name} has no 'code' field")
        return
    if data.get(_APPLIED_KEY):
        _log(log_path, f"{applied_by}: nothing to do - these setup details for '{warehouse.code}' were already "
                       f"applied at {data[_APPLIED_KEY]} (later edits in Admin are kept)")
        return

    db_path_display = "(not yet resolved)"
    try:
        from database.connection import get_db_path

        db_path_display = str(get_db_path())
        from database import warehouse_repository
        from database.exceptions import CapacityBelowUsageError, DuplicateWarehouseCodeError, WarehouseNotFoundError

        def _update() -> str:
            # A sidecar can't know that an administrator switched this
            # warehouse off since setup: keep its current active flag.
            existing = warehouse_repository.get_by_code(warehouse.code)
            warehouse.is_active = existing.is_active
            note = ""
            try:
                warehouse_repository.update(warehouse)
            except CapacityBelowUsageError as exc:
                # The warehouse already holds more than the sidecar's capacity:
                # apply everything else and keep the current capacity.
                warehouse.capacity_units = existing.capacity_units
                warehouse_repository.update(warehouse)
                note = f" (capacity left at {existing.capacity_units}: {exc})"
            kept = "" if existing.is_active else " (it is inactive in Admin, so it stays inactive)"
            return f"updated '{warehouse.code}' ('{warehouse.name}') with the latest setup details{kept}{note}"

        try:
            warehouse_repository.get_by_code(warehouse.code)
            outcome = _update()
        except WarehouseNotFoundError:
            try:
                warehouse_repository.create(warehouse)
                outcome = f"registered warehouse '{warehouse.code}' ('{warehouse.name}')"
            except DuplicateWarehouseCodeError:  # Admin and the Depot applied it at the same moment
                outcome = _update()
    except Exception as exc:
        _log(log_path, f"{applied_by}: FAILED to register '{warehouse.code}': {type(exc).__name__}: {exc} "
                       f"(database: {db_path_display})")
        return

    data[_APPLIED_KEY] = _now_utc()
    try:
        sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(log_path, f"{applied_by}: {outcome} (database: {db_path_display})")
    except OSError as exc:
        _log(log_path, f"{applied_by}: {outcome} (database: {db_path_display}) - but couldn't mark "
                       f"{sidecar.name} as applied ({exc}), so it will be applied again next launch")


def register_pending_warehouse() -> None:
    """depot_app startup hook: apply this instance's own sidecar."""
    exe_path = _running_exe_path()
    if exe_path is None:
        return
    sidecar = warehouse_sidecar_path_for(exe_path)
    if sidecar.exists():
        apply_warehouse_sidecar(sidecar, applied_by=exe_path.name)


def register_warehouse_sidecars_beside_this_exe() -> None:
    """admin_app startup hook: apply every Depot sidecar in Admin's folder."""
    exe_path = _running_exe_path()
    if exe_path is None:
        return
    try:
        sidecars = sorted(exe_path.parent.glob("*" + _SIDECAR_SUFFIX))
    except OSError:
        return
    for sidecar in sidecars:
        try:
            apply_warehouse_sidecar(sidecar, applied_by=exe_path.name)
        except Exception:
            continue


def resolve_this_depot_warehouse(identity: Warehouse | None = None) -> Warehouse:
    """Which warehouse this depot instance is. With a sidecar identity:
    that warehouse's database row (created from the sidecar if applying
    it failed earlier). Without one: warehouse_repository.ensure_default().
    If the database can't be reached at all, the identity (or a bare
    WH-01) is returned as-is so the depot can still open and show why
    its data calls fail."""
    identity = identity if identity is not None else load_warehouse_identity()
    try:
        from database import warehouse_repository
        from database.exceptions import DuplicateWarehouseCodeError, WarehouseNotFoundError

        if identity is None:
            return warehouse_repository.ensure_default()
        try:
            return warehouse_repository.get_by_code(identity.code)
        except WarehouseNotFoundError:
            try:
                return warehouse_repository.create(identity)
            except DuplicateWarehouseCodeError:
                return warehouse_repository.get_by_code(identity.code)
    except Exception:
        if identity is not None:
            return identity
        return Warehouse(code="WH-01", name="Main warehouse")  # = warehouse_repository.DEFAULT_*
