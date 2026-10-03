"""Cross-platform, frozen-aware path resolution.

Three concerns live here:

1. Bundled resources (e.g. database/schema.sql) need a different base
   path once PyInstaller has packaged the app - resource_path() handles
   both cases so callers never write their own `if getattr(sys, 'frozen'...)`
   check.
2. The shared database + a small config.json live OUTSIDE any one app's
   own install folder, since pos_app, depot_app, and admin_app are
   installed/deployed independently. get_db_path() / set_db_path() read
   and write that config.json so all three apps agree on where
   shared_backend.db is, regardless of how any one of them got onto the
   machine.
3. THIS PROJECT SHIPS TWO DIFFERENT DEPLOYMENT MODES, and config.json can
   legitimately live in two different places depending on which one
   produced a given install - see _config_path() below for the priority
   order between them:
     - The per-app Inno Setup installers (pos_app/installer/, etc., built
       via each app's onedir .spec + build_exe.bat) install to their own
       Program Files folder with no config.json of their own - those
       apps fall back to the machine-wide %ProgramData%\\<APP_DATA_DIR_NAME>
       location, get_shared_data_dir() below.
     - deploy_system.py (repo root) builds onefile .exes and drops one
       config.json next to all of them in a flat GPUSA/dist/ folder -
       when you copy an instance (e.g. POS_2.exe) out to its target
       machine, copy config.json alongside it, and that exe-adjacent file
       is used instead of %ProgramData%.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from shared.constants import APP_DATA_DIR_NAME, DATABASE_FILENAME

_REPO_ROOT = Path(__file__).resolve().parent.parent  # meaningful only when NOT frozen
_CONFIG_FILENAME = "config.json"


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource, whether running from source or as a frozen .exe.

    Pass path segments relative to the repository root, e.g.
    resource_path("database", "schema.sql"). The identical relative path
    must also be listed in shared/build_manifest.BUNDLED_DATA_FILES (read
    by both the per-app .spec files and deploy_system.py) so it lands at
    the same relative location under sys._MEIPASS at runtime.
    """
    base = Path(getattr(sys, "_MEIPASS", _REPO_ROOT))
    return base.joinpath(*parts)


def default_shared_data_dir() -> Path:
    """Pure computation of get_shared_data_dir()'s path - no filesystem
    side effect (does not create the directory).

    Exists separately from get_shared_data_dir() so callers that only
    need to SHOW or validate the default (e.g. deploy_system.py's
    database-path prompt) don't accidentally create a %ProgramData%
    folder on the machine running the build, which may not even be a
    machine any app is deployed to.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    elif sys.platform == "darwin":
        # Where a Mac app keeps its data. Per-user: every app on this Mac
        # run by the same login (POS, Depot and Admin) sees the same file.
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / APP_DATA_DIR_NAME


def get_shared_data_dir() -> Path:
    """The machine-wide folder apps using the %ProgramData% fallback read/write to.

    Defaults to %ProgramData%\\<APP_DATA_DIR_NAME> on Windows (falls back
    to a dotfile under the user's home directory on other platforms, for
    local development off Windows). Created on first use - see
    default_shared_data_dir() for the side-effect-free version.

    Note: creating a new subfolder under C:\\ProgramData does not
    normally require admin rights on a default Windows install, but a
    locked-down store PC's Group Policy could restrict it - if apps
    installed with `PrivilegesRequired=lowest` (see the .iss installer
    scripts) fail to create this folder on first launch, that ACL is why.
    """
    data_dir = default_shared_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _app_bundle_dir() -> Path | None:
    """On macOS, the folder that CONTAINS the running ".app" (so a config.json
    dropped next to GPUSA-POS.app is found), or None when not inside a bundle.
    Inside a bundle sys.executable is .../X.app/Contents/MacOS/X, and
    writing next to that would be inside the signed app itself."""
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent.parent
    return None


def _exe_adjacent_config_path() -> Path | None:
    """The config.json living next to the running app, if there is one.

    Only meaningful when frozen (PyInstaller sets sys.executable to the
    app's own path; in a normal `python` run sys.executable is the
    interpreter, and treating ITS directory as a config location would be
    wrong) - see deploy_system.py, which is what actually writes a
    config.json into this location (GPUSA/dist/, alongside every .exe it
    produces). For a macOS .app that location is the folder holding the
    .app, not the bundle's inside.
    """
    if not getattr(sys, "frozen", False):
        return None
    bundle_dir = _app_bundle_dir() if sys.platform == "darwin" else None
    return (bundle_dir or Path(sys.executable).resolve().parent) / _CONFIG_FILENAME


def _config_path() -> Path:
    """Where THIS process reads/writes config.json.

    Priority: an exe-adjacent config.json (deploy_system.py's flat
    onefile deployment) if one actually exists next to the running .exe,
    otherwise the %ProgramData% fallback (the per-app installers'
    deployment, or plain `python -m pos_app.main` during development).
    Checked by existence rather than decided once at import time, since
    the same source tree serves both deployment modes - which one applies
    depends only on whether deploy_system.py's output is what's actually
    running.
    """
    exe_adjacent = _exe_adjacent_config_path()
    if exe_adjacent is not None and exe_adjacent.exists():
        return exe_adjacent
    return get_shared_data_dir() / _CONFIG_FILENAME


def _load_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_config(config: dict) -> None:
    _config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_db_path() -> Path:
    """Return the configured shared_backend.db location.

    Reads config.json via _config_path() (see its docstring for the
    exe-adjacent-vs-%ProgramData% priority). If no config.json exists yet
    at all (neither exe-adjacent nor in %ProgramData%), writes a default
    one pointing at get_shared_data_dir()/shared_backend.db - this is the
    path taken when an app runs standalone with no config.json ever
    provided (deploy_system.py normally always supplies one).
    """
    config = _load_config()
    if "db_path" not in config:
        config["db_path"] = str(get_shared_data_dir() / DATABASE_FILENAME)
        _save_config(config)
    return Path(config["db_path"])


def set_db_path(new_path: Path) -> None:
    """Repoint this installation at a different shared_backend.db location.

    TODO: wire this up to Admin's future Data Location setting
    (admin_app/gui - not yet built). Takes effect on the next launch of
    each app, not the currently running one - the existing sqlite3
    connection doesn't move. Writes to whichever config.json
    _config_path() currently resolves to (exe-adjacent if present).
    """
    config = _load_config()
    config["db_path"] = str(new_path)
    _save_config(config)
