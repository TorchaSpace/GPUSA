#!/usr/bin/env python3
"""Master Builder & Setup Script.

Interactive CLI wizard that builds standalone, single-file .exe copies of
admin_app, pos_app, and depot_app via PyInstaller, and drops as many
renamed copies of each as you ask for into one flat GPUSA/dist/ folder,
alongside a single shared config.json pointing every copy at the same
database.

THIS IS A SECOND, SEPARATE DEPLOYMENT PATH from the per-app onedir
.spec files + Inno Setup installers (pos_app/build_exe.bat,
pos_app/installer/pos_app_installer.iss, and their depot_app/admin_app
equivalents - see architecture.md's Packaging section). Those produce
one clean, installed-with-an-uninstaller app per machine, each in its
own Program Files folder. This script instead mass-produces onefile,
uninstaller-less copies meant for flat manual distribution - e.g. build
once here, then copy POS_2.exe (plus dist/config.json) straight onto
till #2's desktop, no installer wizard involved. Use whichever fits how
you're actually rolling machines out; they are not mutually exclusive,
and this script does not touch or replace the other one.

Run from anywhere - it locates the repository root from its own file
location, not the current working directory:
    python deploy_system.py

Requires the same environment as pos_app/build_exe.bat: activate the
venv you ran `pip install -r requirements.txt` into first. This script
always invokes PyInstaller as `sys.executable -m PyInstaller` (never a
bare `pyinstaller` on PATH) specifically to avoid the "built successfully
but the .exe says No module named 'PySide6' at launch" failure mode that
happens when a `pyinstaller` on PATH silently belongs to a different
Python than the one packages were installed into - see
pos_app/build_exe.bat's header comment for the full story.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))  # so `import shared` resolves regardless of CWD

from shared.build_manifest import BUNDLED_DATA_FILES  # noqa: E402
from shared.constants import DATABASE_FILENAME  # noqa: E402
from shared.dealership_bootstrap import sidecar_path_for  # noqa: E402
from shared.models import DEALERSHIP_REGIONS  # noqa: E402
from shared.paths import default_shared_data_dir  # noqa: E402
from shared.warehouse_bootstrap import warehouse_sidecar_path_for  # noqa: E402

DIST_DIR = REPO_ROOT / "dist"
BUILD_DIR = REPO_ROOT / "_deploy_build"  # scratch space - wiped clean at the end


@dataclass(frozen=True)
class Role:
    key: str
    prompt_label: str
    entry_script: str  # relative to REPO_ROOT
    prefix: str  # output filename prefix, e.g. "Admin" -> Admin_1.exe


ROLES = [
    Role("admin", "Admin Dashboard", "admin_app/main.py", "Admin"),
    Role("pos", "POS (Branch)", "pos_app/main.py", "POS"),
    Role("depot", "Depot (Warehouse)", "depot_app/main.py", "Depot"),
]


def check_environment() -> None:
    """Fail fast and clearly, before spending minutes in PyInstaller, if
    the interpreter running THIS script can't build these apps.
    """
    missing = []
    for module_name in ("PySide6", "PyInstaller"):
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)
    if missing:
        print(
            "ERROR: this Python is missing: " + ", ".join(missing) + "\n"
            "Activate the venv you ran `pip install -r requirements.txt` into,\n"
            "then re-run:\n    python deploy_system.py"
        )
        sys.exit(1)

    if sys.platform != "win32":
        print(
            "WARNING: this project's deployment targets Windows (.exe,\n"
            "%ProgramData%, Inno Setup). PyInstaller cannot cross-compile -\n"
            "anything built here will be for THIS platform, not Windows.\n"
        )


def prompt_int(question: str) -> int:
    while True:
        raw = input(f"{question} ").strip()
        if raw == "":
            return 0
        try:
            value = int(raw)
        except ValueError:
            print("  Please enter a whole number (or press Enter for 0).")
            continue
        if value < 0:
            print("  Please enter zero or a positive number.")
            continue
        return value


def prompt_db_path() -> str:
    """Loops until the answer is either empty (accept the default) or an
    absolute path - previously a non-absolute answer only printed a
    warning and was used anyway, which silently wrote a relative path
    like a stray "y" (typed at the wrong prompt, or meant as a yes/no
    answer) straight into config.json. Every app then resolves that
    relative path against wherever it happens to be double-clicked from,
    creating a brand new, empty, disconnected database there instead of
    the intended shared one - which looks exactly like "my dealerships/
    sales disappeared" rather than an obvious setup mistake. Rejecting it
    up front, with the same wizard still on screen to retype it, is far
    cheaper than debugging that after the fact.
    """
    default_path = default_shared_data_dir() / DATABASE_FILENAME
    while True:
        raw = input(
            "What should be the shared absolute path for the SQLite database?\n"
            f"(Press Enter for default: {default_path})\n> "
        ).strip()
        if not raw:
            return str(default_path)
        # PureWindowsPath, not the platform-native Path: this only ever
        # targets a Windows deployment (see check_environment()'s warning
        # above), so "is this absolute" should always mean Windows rules
        # (C:\... or \\SERVER\share\...) - regardless of what OS happens
        # to be running this script itself.
        if PureWindowsPath(raw).is_absolute():
            return raw
        print(
            f"  {raw!r} isn't an absolute path (e.g. {default_path} or a UNC path like "
            f"\\\\SERVER\\share\\shared_backend.db) - that's almost never what you meant to type "
            f"here, so it hasn't been accepted. Press Enter for the default above, or type a "
            f"full absolute path.\n"
        )


def prompt_dealership_details(count: int) -> list[dict]:
    """Ask once whether to type real per-terminal dealership details now,
    then either prompt each POS terminal for the same fields admin_app's
    Dealerships page edits (code/name/region/city/manager), or auto-fill
    a placeholder for all of them.

    Every POS instance gets an entry back either way - what actually
    turns an entry into a real `dealerships` row is
    shared/dealership_bootstrap.py, read by that specific POS terminal's
    own .exe the first time IT launches (not by this script, which may
    be running on a machine that can't even reach the shared database
    yet - see write_dealership_sidecars()'s docstring).
    """
    print(
        f"\nEach POS terminal you're about to build will appear as an active\n"
        f"dealership in Admin as soon as you open Admin from the same folder\n"
        f"- no manual data entry required unless you want it.\n"
    )
    fill_now = input(
        f"Enter real dealership details for these {count} POS terminal(s) now? [y/N] "
    ).strip().lower() in ("y", "yes")

    entries = []
    for i in range(1, count + 1):
        default_code = f"POS-{i}"
        default_name = f"POS Terminal {i}"
        if not fill_now:
            entries.append(
                {"code": default_code, "name": default_name, "region": DEALERSHIP_REGIONS[0],
                 "city": "Unspecified", "manager_name": None}
            )
            continue

        print(f"\n-- POS terminal #{i} (POS_{i}.exe) --")
        code = input(f"  Dealership code [{default_code}]: ").strip() or default_code
        name = input(f"  Dealership / branch name [{default_name}]: ").strip() or default_name
        print("  Region: " + ", ".join(f"{n + 1}={r}" for n, r in enumerate(DEALERSHIP_REGIONS)))
        region_raw = input(f"  Choose 1-{len(DEALERSHIP_REGIONS)} [1={DEALERSHIP_REGIONS[0]}]: ").strip()
        try:
            region = DEALERSHIP_REGIONS[int(region_raw) - 1] if region_raw else DEALERSHIP_REGIONS[0]
        except (ValueError, IndexError):
            print(f"    Not a valid choice - using {DEALERSHIP_REGIONS[0]}.")
            region = DEALERSHIP_REGIONS[0]
        city = input("  City [Unspecified]: ").strip() or "Unspecified"
        manager = input("  Manager name (optional): ").strip() or None
        entries.append({"code": code, "name": name, "region": region, "city": city, "manager_name": manager})
    return entries


def write_dealership_sidecars(dist_dir: Path, exe_suffix: str, entries: list[dict]) -> list[str]:
    """Write one "<POS_i>.dealership.json" file per entry, next to the
    matching POS_i.exe in dist_dir.

    Deliberately NOT written straight into the database from here: the
    db_path just entered in prompt_db_path() may be a UNC/network path
    meant for a DIFFERENT machine than the one currently running this
    build script, and may not even be reachable yet. Each sidecar instead
    travels with its own .exe (same convention as config.json) and is
    read by shared/dealership_bootstrap.py the first time THAT terminal
    actually launches somewhere the shared database really is reachable.
    """
    written = []
    for i, entry in enumerate(entries, start=1):
        exe_path = dist_dir / f"POS_{i}{exe_suffix}"
        sidecar_path = sidecar_path_for(exe_path)
        sidecar_path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        written.append(sidecar_path.name)
    return written


def prompt_optional_count(question: str) -> int | None:
    """A whole number above 0, or empty for "not set". Re-asks on anything else."""
    while True:
        raw = input(question).strip()
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
        print("    Please enter a whole number above 0, or leave it empty.")


def prompt_warehouse_details(count: int) -> list[dict]:
    """Ask once whether to type real per-Depot warehouse details now, then
    either prompt each Depot instance for the fields Admin > Warehouses
    edits (code / name / city / capacity in units / loading docks), or
    auto-fill placeholders (WH-01, WH-02, ...). Each Depot instance IS one
    warehouse: whatever is entered here becomes its row the first time
    Admin (from the same folder) or that Depot launches - see
    shared/warehouse_bootstrap.py."""
    print(
        "\nEach Depot instance runs one warehouse. It will appear in\n"
        "Admin > Warehouses as soon as you open Admin from the same folder.\n"
    )
    fill_now = input(
        f"Enter real warehouse details for these {count} Depot instance(s) now? [y/N] "
    ).strip().lower() in ("y", "yes")

    entries = []
    for i in range(1, count + 1):
        default_code = f"WH-{i:02d}"
        default_name = f"Warehouse {i}"
        if not fill_now:
            entries.append({"code": default_code, "name": default_name, "city": "", "capacity_units": None, "docks": 0})
            continue
        print(f"\n-- Depot #{i} (Depot_{i}.exe) --")
        code = input(f"  Warehouse code [{default_code}]: ").strip() or default_code
        name = input(f"  Warehouse name [{default_name}]: ").strip() or default_name
        city = input("  City (optional): ").strip()
        capacity = prompt_optional_count("  Capacity in units of stock (optional): ")
        docks = prompt_optional_count("  Loading docks (optional): ") or 0
        entries.append({"code": code, "name": name, "city": city, "capacity_units": capacity, "docks": docks})
    return entries


def write_warehouse_sidecars(dist_dir: Path, exe_suffix: str, entries: list[dict]) -> list[str]:
    """One "Depot_<i>.warehouse.json" per entry next to Depot_<i>.exe -
    not written to the database from here, same reasoning as
    write_dealership_sidecars()."""
    written = []
    for i, entry in enumerate(entries, start=1):
        sidecar_path = warehouse_sidecar_path_for(dist_dir / f"Depot_{i}{exe_suffix}")
        sidecar_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(sidecar_path.name)
    return written

def run_pyinstaller_build(role: Role) -> Path:
    """Build ONE onefile .exe for `role` into BUILD_DIR, return its path.

    Built exactly once per role regardless of how many numbered copies
    were requested - the copies made afterward (see copy_instances()) are
    plain filesystem copies of this one binary, not separate builds.
    """
    entry_path = REPO_ROOT / role.entry_script
    dist_dir = BUILD_DIR / "dist"
    work_dir = BUILD_DIR / "work"
    spec_dir = BUILD_DIR / "spec"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(entry_path),
        "--onefile",
        "--noconsole",
        "--name", role.prefix,
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--paths", str(REPO_ROOT),
        "--noconfirm",
        "--clean",
    ]
    for rel_path, dest in BUNDLED_DATA_FILES:
        cmd += ["--add-data", f"{REPO_ROOT / rel_path}{os.pathsep}{dest}"]

    print(f"\nBuilding {role.prompt_label} ({role.entry_script}) ...")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(f"\nERROR: PyInstaller failed for {role.prompt_label} (exit {result.returncode}). Stopping.")
        sys.exit(result.returncode)

    built_exe = dist_dir / f"{role.prefix}.exe"
    if not built_exe.exists():
        built_exe = dist_dir / role.prefix  # non-Windows build machine: no .exe suffix
    if not built_exe.exists():
        print(f"ERROR: expected build output not found at {built_exe}")
        sys.exit(1)
    return built_exe


def copy_instances(built_exe: Path, role: Role, count: int) -> list[str]:
    written = []
    for i in range(1, count + 1):
        dest_name = f"{role.prefix}_{i}{built_exe.suffix}"
        shutil.copy2(built_exe, DIST_DIR / dest_name)
        written.append(dest_name)
    return written


def main() -> None:
    print("=== POS / Inventory System - Master Builder & Setup ===\n")
    check_environment()

    counts = {role.key: prompt_int(f"How many {role.prompt_label} instances do you want to generate?")
              for role in ROLES}

    if sum(counts.values()) == 0:
        print("\nNo instances requested for any app - nothing to build. Exiting.")
        return

    db_path = prompt_db_path()

    print("\nPlan:")
    for role in ROLES:
        print(f"  {role.prompt_label}: {counts[role.key]}")
    print(f"  Shared database path: {db_path}")
    print(f"  Output folder: {DIST_DIR}\n")

    if DIST_DIR.exists():
        print(f"Clearing existing {DIST_DIR} ...")
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    all_written = []
    for role in ROLES:
        count = counts[role.key]
        if count == 0:
            continue
        built_exe = run_pyinstaller_build(role)
        written = copy_instances(built_exe, role, count)
        all_written.extend(written)
        print(f"  -> {', '.join(written)}")

        if role.key == "pos":
            entries = prompt_dealership_details(count)
            sidecars = write_dealership_sidecars(DIST_DIR, built_exe.suffix, entries)
            all_written.extend(sidecars)
            print(f"  -> {', '.join(sidecars)}")
        elif role.key == "depot":
            entries = prompt_warehouse_details(count)
            sidecars = write_warehouse_sidecars(DIST_DIR, built_exe.suffix, entries)
            all_written.extend(sidecars)
            print(f"  -> {', '.join(sidecars)}")

    config_path = DIST_DIR / "config.json"
    config_path.write_text(json.dumps({"db_path": db_path}, indent=2), encoding="utf-8")
    all_written.append(config_path.name)

    print("\nCleaning up build cache ...")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)

    print(f"\nDone. {DIST_DIR} now contains:")
    for name in sorted(all_written):
        print(f"  {name}")
    print(
        "\nEach .exe looks for config.json in its OWN folder at startup\n"
        "(see shared/paths.py's exe-adjacent lookup) - so when you move an\n"
        "instance out to its target machine (e.g. POS_2.exe to till #2),\n"
        "take config.json along with it, or otherwise ensure a config.json\n"
        "with the same db_path ends up next to it."
    )
    if counts.get("pos", 0):
        print(
            "\nThe POS terminals above will show up as active dealerships\n"
            "the next time you open Admin from THIS folder - no need to open\n"
            "each POS terminal first. (A POS .exe copied to another machine\n"
            "registers itself on its own first launch instead - take its\n"
            "POS_N.dealership.json along with config.json.) Re-running setup\n"
            "with new details for an existing code updates that dealership;\n"
            "edits you make afterwards in Admin > Dealerships are kept.\n"
            "\n"
            "Each POS_N.dealership.log next to a terminal records what\n"
            "happened to it (registered / updated / failed and why) and\n"
            "which database file was used."
        )
    if counts.get("depot", 0):
        print(
            "\nThe Depot instances above will show up in Admin > Warehouses\n"
            "the next time you open Admin from THIS folder (a Depot .exe moved\n"
            "elsewhere registers itself on first launch - take its\n"
            "Depot_N.warehouse.json along). Depot_N.warehouse.log records what\n"
            "happened. Stock that existed before warehouses were tracked shows\n"
            "as 'Unassigned' in Admin > Warehouses until you place it."
        )


if __name__ == "__main__":
    main()
