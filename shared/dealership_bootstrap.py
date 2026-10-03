"""Turning the setup wizard's per-POS dealership details into real
`dealerships` rows.

The two setup wizards (deploy_system.py, the developer-only master
builder, and installer.py, the standalone setup.exe) write a small
"POS_<n>.dealership.json" sidecar file next to each POS instance's .exe,
holding exactly the details typed in during setup (code/name/region/
city/manager). THIS module is what turns those files into database rows.

Who applies a sidecar, and when:
- admin_app, at startup, applies EVERY sidecar sitting in its own folder
  (register_sidecars_beside_this_exe()). The wizards put Admin_1.exe and
  POS_1..N.exe in the same folder, so opening Admin right after setup is
  enough for every POS terminal from that run to show up as an active
  dealership - nobody has to open each POS terminal first. This is the
  path that actually delivers "terminals created during setup appear in
  Admin"; an earlier version of this module only ran inside each POS
  app, which meant nothing showed up in Admin until every single POS
  terminal had been launched at least once.
- pos_app, at startup, applies its OWN sidecar
  (register_pending_dealership()) - the fallback for a POS .exe that's
  been copied out to its till on another machine, away from Admin.

"Latest setup wins, then hands off to Admin." A sidecar the wizard has
just written has no "applied_at" key. The first app to see it creates the
dealership - or, if that code already exists (e.g. from an earlier setup
run with different details), UPDATES the existing row to match what was
typed in this time - and then stamps "applied_at" into the sidecar.
Every later launch sees the stamp and leaves the row alone, so edits made
afterwards in Admin > Dealerships are never clobbered by a relaunch.
Re-running setup writes fresh, unstamped sidecars, so its new details
are applied again. (The earlier version never updated an existing code
at all, which is why re-running setup with new details for code 001
left the old details showing in Admin.)

Also exposes load_dealership_identity(), a read-only sibling that
pos_app's header uses to show the terminal's own dealership name/location
in place of the generic "GPUSA" placeholder - see its docstring.

Non-fatal, never silent: every failure here is swallowed - a bootstrap
hiccup must never stop a cashier from opening the till or an admin from
opening the dashboard - but each outcome for a sidecar (registered /
updated / already applied / failed and why) is appended to a
"POS_<n>.dealership.log" next to it, including which database file was
used. The built apps are --noconsole, so that log is the only way to
see what happened.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SIDECAR_SUFFIX = ".dealership.json"
_LOG_SUFFIX = ".dealership.log"
_APPLIED_KEY = "applied_at"


def sidecar_path_for(exe_path: Path) -> Path:
    """The sidecar filename a setup wizard writes for a given built .exe
    (e.g. POS_2.exe -> POS_2.dealership.json). Exposed so deploy_system.py
    and installer.py compute the exact same name this module looks for."""
    return exe_path.with_name(exe_path.stem + _SIDECAR_SUFFIX)


def log_path_for(exe_path: Path) -> Path:
    """The diagnostic log for a given POS .exe (e.g. POS_2.exe ->
    POS_2.dealership.log). Safe to delete at any time."""
    return exe_path.with_name(exe_path.stem + _LOG_SUFFIX)


def _log_path_for_sidecar(sidecar: Path) -> Path:
    name = sidecar.name
    stem = name[: -len(_SIDECAR_SUFFIX)] if name.endswith(_SIDECAR_SUFFIX) else sidecar.stem
    return sidecar.with_name(stem + _LOG_SUFFIX)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(log_path: Path, message: str) -> None:
    """Best-effort append of one timestamped line. Never raises."""
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{_now_utc()}] {message}\n")
    except OSError:
        pass


def _running_exe_path() -> Path | None:
    """The currently running .exe, or None in a `python -m ...` dev run
    (where sys.executable is the interpreter, not a wizard-built
    instance) - same rule as shared/paths.py's _exe_adjacent_config_path()."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def _running_sidecar_path() -> Path | None:
    exe_path = _running_exe_path()
    if exe_path is None:
        return None
    return sidecar_path_for(exe_path)


def load_dealership_identity() -> dict | None:
    """Read this POS instance's own sidecar (if any) for DISPLAY only -
    pos_app's header shows the terminal's dealership name and
    "{city} · {region}" instead of the generic placeholder. Never touches
    the database. Returns None when there's nothing to show (dev run, no
    sidecar, corrupt/incomplete sidecar) - callers keep their placeholder.
    """
    path = _running_sidecar_path()
    if path is None or not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    code = str(data.get("code") or "").strip()
    if not code:
        return None

    name = str(data.get("name") or code)
    city = str(data.get("city") or "").strip()
    region = str(data.get("region") or "").strip()
    location_line = " · ".join(part for part in (city, region) if part) or "Unspecified location"

    return {"code": code, "name": name, "location_line": location_line}


def apply_sidecar(sidecar: Path, applied_by: str) -> None:
    """Apply one sidecar file to the database - create the dealership, or
    update an existing one with the same code to the sidecar's details -
    unless it has already been applied. Never raises; logs every outcome
    next to the sidecar. See the module docstring for the rules.
    """
    log_path = _log_path_for_sidecar(sidecar)

    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log(log_path, f"{applied_by}: skipped - could not read/parse {sidecar.name} ({exc})")
        return
    if not isinstance(data, dict):
        _log(log_path, f"{applied_by}: skipped - {sidecar.name} doesn't contain a JSON object")
        return

    code = str(data.get("code") or "").strip()
    if not code:
        _log(log_path, f"{applied_by}: skipped - {sidecar.name} has no 'code' field")
        return

    if data.get(_APPLIED_KEY):
        _log(
            log_path,
            f"{applied_by}: nothing to do - these setup details for '{code}' were already "
            f"applied at {data[_APPLIED_KEY]} (later edits in Admin are kept)",
        )
        return

    db_path_display = "(not yet resolved)"
    try:
        # Imported lazily so a missing/corrupt sidecar (the common no-op
        # case) never pays for the database import. get_db_path comes from
        # database.connection - the exact reference get_connection() uses -
        # so the logged path is the one actually written to.
        from database.connection import get_db_path

        db_path_display = str(get_db_path())

        from database.dealership_repository import create, get_by_code, update
        from database.exceptions import DealershipNotFoundError, DuplicateDealershipCodeError
        from shared.models import DEALERSHIP_REGIONS, Dealership

        region = str(data.get("region") or DEALERSHIP_REGIONS[0])
        if region not in DEALERSHIP_REGIONS:
            region = DEALERSHIP_REGIONS[0]
        name = str(data.get("name") or code)
        city = str(data.get("city") or "Unspecified")
        manager_name = data.get("manager_name") or None

        def _update_existing() -> str:
            existing = get_by_code(code)
            previous_name = existing.name
            existing.name = name
            existing.region = region
            existing.city = city
            existing.manager_name = manager_name
            existing.is_active = True
            update(existing)
            if previous_name == name:
                return f"updated '{code}' ('{name}') with the latest setup details"
            return f"updated '{code}' with the latest setup details (name was '{previous_name}', now '{name}')"

        try:
            get_by_code(code)
            outcome = _update_existing()
        except DealershipNotFoundError:
            try:
                create(
                    Dealership(
                        code=code,
                        name=name,
                        region=region,
                        city=city,
                        manager_name=manager_name,
                        is_active=True,
                    )
                )
                outcome = f"registered '{code}' ('{name}') as an active dealership"
            except DuplicateDealershipCodeError:
                # Admin and a POS terminal applied the same fresh sidecar at
                # the same moment - the other one created it; just update.
                outcome = _update_existing()
    except Exception as exc:
        _log(
            log_path,
            f"{applied_by}: FAILED to register '{code}': {type(exc).__name__}: {exc} "
            f"(database: {db_path_display})",
        )
        return

    data[_APPLIED_KEY] = _now_utc()
    try:
        sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(log_path, f"{applied_by}: {outcome} (database: {db_path_display})")
    except OSError as exc:
        _log(
            log_path,
            f"{applied_by}: {outcome} (database: {db_path_display}) - but couldn't mark "
            f"{sidecar.name} as applied ({exc}), so it will be applied again next launch",
        )


def register_pending_dealership() -> None:
    """pos_app startup hook: apply this POS instance's own sidecar, if it
    has one. No-op in a dev run or for a POS .exe with no sidecar."""
    exe_path = _running_exe_path()
    if exe_path is None:
        return
    sidecar = sidecar_path_for(exe_path)
    if not sidecar.exists():
        return
    apply_sidecar(sidecar, applied_by=exe_path.name)


def register_sidecars_beside_this_exe() -> None:
    """admin_app startup hook: apply every POS sidecar in the running
    .exe's own folder - i.e. every POS terminal the setup wizard just
    created alongside this Admin instance. No-op in a dev run."""
    exe_path = _running_exe_path()
    if exe_path is None:
        return
    try:
        sidecars = sorted(exe_path.parent.glob("*" + _SIDECAR_SUFFIX))
    except OSError:
        return
    for sidecar in sidecars:
        try:
            apply_sidecar(sidecar, applied_by=exe_path.name)
        except Exception:  # apply_sidecar never raises, but Admin must open regardless
            continue
