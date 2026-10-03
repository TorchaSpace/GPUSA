#!/usr/bin/env python3
"""Deployment Wizard - the interactive script behind setup.exe.

THIS FILE HAS ZERO DEPENDENCY ON THE REST OF THE REPOSITORY, BY DESIGN.
It never imports `shared`, `database`, or PySide6 - build_setup.py (Stage 1,
the developer-only master builder) compiles THIS file alone into setup.exe,
and then embeds the three already-built application .exe files inside that
same setup.exe as bundled data. So at the point this script runs (as the
compiled setup.exe, on a branch manager's machine that may not even have
Python installed), there is no `shared/` package on disk to import - only
whatever PyInstaller extracted alongside this script.

How setup.exe finds its embedded payload:
    PyInstaller's onefile mode extracts everything passed via --add-data
    into a temporary folder at startup, and points sys._MEIPASS at it for
    the lifetime of the process (see payload_dir() below). build_setup.py
    adds each of the three built app .exe files under a "payload/"
    subfolder inside the bundle, so sys._MEIPASS/payload/Admin.exe,
    .../POS.exe, and .../Depot.exe are what this script looks for.
    _MEIPASS is deleted automatically when the process exits - which is
    exactly why this script's job is to COPY those exes out to a
    permanent location (Path.cwd(), where the user double-clicked
    setup.exe) before it finishes.

What this script does, in order:
    1. Locates its embedded payload folder (sys._MEIPASS/payload).
    2. Asks how many Admin / POS / Depot instances to install.
    3. Asks for the shared database path (with a multi-machine warning -
       see prompt_db_path()).
    4. Copies/renames the requested number of each payload .exe into the
       CURRENT WORKING DIRECTORY (wherever setup.exe was double-clicked -
       e.g. Admin_1.exe, POS_1.exe, POS_2.exe, Depot_1.exe).
    5. Writes a single shared config.json next to them.
    6. If any POS instances were installed, asks whether to type real
       dealership details (code/name/region/city/manager - the same
       fields admin_app's Dealerships page edits) for each one now, or
       accept an auto-filled placeholder, and writes one
       "POS_N.dealership.json" sidecar per POS instance either way (see
       prompt_dealership_details()/write_dealership_sidecars()). That
       Admin (opened from the same folder) or the terminal's own pos_app
       reads the sidecars back and registers them as active dealerships -
       see shared/dealership_bootstrap.py (part of the main repo, not
       this dependency-free script) for the actual database write.
    7. Likewise for Depot instances: each one IS a warehouse, so it asks
       for code/name/city/capacity (units)/docks per Depot (or fills
       WH-01, WH-02, ... placeholders) and writes "Depot_N.warehouse.json"
       sidecars, which Admin or that Depot turns into Admin > Warehouses
       rows - see shared/warehouse_bootstrap.py.

Because setup.exe is an interactive console wizard (not --noconsole, see
build_setup.py's comment on why), its console window closes the instant
the process exits - so every exit path in this script runs through
pause_and_exit() first, to give the person who just double-clicked it a
chance to actually read the summary or the error before the window
vanishes.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path, PureWindowsPath

# Must exactly match build_setup.py's PAYLOAD_ROLES: the (key, label, prefix)
# for each app, where `prefix` is both the --name PyInstaller built each
# payload exe as (so "<prefix>.exe" is what's sitting in payload/) and the
# filename prefix used for each numbered copy this script writes out
# (e.g. prefix "POS" -> POS_1.exe, POS_2.exe).
ROLES = [
    ("admin", "Admin Dashboard", "Admin"),
    ("pos", "POS (Branch)", "POS"),
    ("depot", "Depot (Warehouse)", "Depot"),
]

CONFIG_FILENAME = "config.json"
DEALERSHIP_SIDECAR_SUFFIX = ".dealership.json"
# Must match shared/warehouse_bootstrap.py's _SIDECAR_SUFFIX.
WAREHOUSE_SIDECAR_SUFFIX = ".warehouse.json"

# Must exactly match shared.models.DEALERSHIP_REGIONS. Duplicated here,
# same reasoning as ROLES above: this file has zero dependency on the
# rest of the repo by design (see module docstring), so it can't import
# shared.models like deploy_system.py (its sibling wizard, which prompts
# for the exact same fields the same way) does.
DEALERSHIP_REGIONS = ("Metro", "Coastal", "Valley")


def pause_and_exit(code: int) -> None:
    """Keep the console window open until the user acknowledges, then exit.

    A double-clicked console .exe's window disappears the instant the
    process exits - without this, a person who just double-clicked
    setup.exe would never get to read the success summary or an error
    message; it would flash and vanish. Every exit path in this script
    goes through here instead of calling sys.exit() directly.
    """
    try:
        input("\nPress Enter to close this window.")
    except EOFError:
        # Running non-interactively (e.g. piped input in a test) - nothing
        # to wait on.
        pass
    sys.exit(code)


def payload_dir() -> Path:
    """Locate the embedded payload folder extracted by the PyInstaller bootloader.

    sys._MEIPASS is only set when running as a frozen, extracted onefile
    build - see build_setup.py's build_setup_exe() for how the payload
    exes are placed under a "payload/" subfolder inside the bundle via
    --add-data. If this attribute is missing, this script is being run
    directly with `python installer.py` (not as the compiled setup.exe)
    and there is no extracted payload to find - that's a developer-only
    situation, not something a branch manager would ever hit, so it's
    reported as an error rather than silently falling back to something
    on disk.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        print(
            "ERROR: this script has no embedded payload to install from.\n"
            "installer.py is meant to be run as the compiled setup.exe\n"
            "(built by build_setup.py), not directly with `python installer.py`.\n"
            "If you're the developer testing this, run build_setup.py first\n"
            "and launch the resulting Kurulum/setup.exe instead."
        )
        pause_and_exit(1)
    return Path(meipass) / "payload"


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
    """Ask for the shared database path, with an explicit multi-machine warning.

    Pressing Enter leaves db_path out of config.json entirely (see main())
    rather than this script guessing a default itself - each installed
    app's own shared/paths.get_db_path() already knows how to fill in and
    save a sensible default (a machine-wide %ProgramData% folder) the
    first time it runs with no config.json value present. Duplicating
    that default-computation logic here, in a script that deliberately
    has no import access to shared/paths.py, would just be a second place
    for the two to drift apart - so this script intentionally leaves that
    single detail to the apps themselves.

    The warning matters because that per-machine default is LOCAL to
    whichever machine an app runs on: if every till/workstation for this
    deployment is the SAME physical machine, pressing Enter on all of
    them is fine. But if Admin, POS, and Depot instances from this same
    setup.exe run are meant to end up on DIFFERENT machines sharing one
    database, each one defaulting independently means each one silently
    gets its OWN separate, invisible-to-each-other database - which looks
    like data loss ("my sales aren't showing up in Admin") rather than an
    obvious error.
    """
    print(
        "\nWhat shared database path should these apps use?\n"
        "  - If ALL the apps you're installing right now will run on THIS\n"
        "    SAME machine, you can press Enter - each app will pick a\n"
        "    sensible local default on its first launch.\n"
        "  - If these apps (or copies of this setup.exe's output) will run\n"
        "    on DIFFERENT machines that need to share ONE database, you\n"
        "    MUST type an explicit shared/network path here (e.g. a UNC\n"
        "    path like \\\\SERVER\\POSData\\shared_backend.db). Pressing\n"
        "    Enter in that case means every machine silently gets its own\n"
        "    separate, disconnected database - sales and stock updates on\n"
        "    one till simply will not appear on any other.\n"
    )
    while True:
        raw = input("Shared database path (or press Enter to use each app's default): ").strip()
        if not raw:
            return raw
        # PureWindowsPath, not the platform-native Path: this only ever
        # targets a Windows deployment, so "is this absolute" should
        # always mean Windows rules (C:\... or \\SERVER\share\...),
        # regardless of what OS happens to be running this script itself.
        if PureWindowsPath(raw).is_absolute():
            return raw
        print(
            f"  {raw!r} isn't an absolute path (e.g. C:\\ProgramData\\...\\shared_backend.db, "
            f"or a UNC path like \\\\SERVER\\share\\shared_backend.db) - that's almost never what "
            f"you meant to type here, so it hasn't been accepted. A relative path here would "
            f"silently create a brand new, empty database wherever an app happens to be launched "
            f"from, instead of the shared one - which looks like missing data, not an obvious "
            f"mistake. Press Enter to use each app's own local default, or type a full "
            f"absolute/UNC path.\n"
        )


def prompt_dealership_details(count: int) -> list[dict]:
    """Ask once whether to type real per-terminal dealership details now,
    then either prompt each POS terminal for code/name/region/city/manager
    (the same fields admin_app's Dealerships page edits), or auto-fill a
    placeholder for all of them. Mirrors deploy_system.py's function of
    the same name field-for-field - kept as a separate copy here rather
    than a shared import, per this file's zero-dependency design (see
    module docstring)."""
    print(
        "\nEach POS terminal you're about to install will appear as an active\n"
        "dealership in Admin as soon as you open Admin from the same folder\n"
        "- no manual data entry required unless you want it.\n"
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


def write_dealership_sidecars(target_dir: Path, exe_suffix: str, entries: list[dict]) -> list[str]:
    """Write one "POS_N.dealership.json" file per entry, next to the
    matching POS_N.exe in target_dir. Deliberately NOT written straight
    into the database from here - same reasoning as
    deploy_system.py's write_dealership_sidecars(): the db_path just
    entered may point at a machine other than the one setup.exe is
    running on right now. shared/dealership_bootstrap.py (bundled inside
    each POS payload exe, not this script) reads the sidecar back and
    does the actual database write the first time that terminal launches
    somewhere the shared database really is reachable."""
    written = []
    for i, entry in enumerate(entries, start=1):
        sidecar_path = target_dir / f"POS_{i}{DEALERSHIP_SIDECAR_SUFFIX}"
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
    shared/warehouse_bootstrap.py (bundled in the Depot/Admin payloads,
    not this script). Mirrors deploy_system.py's copy field-for-field."""
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


def write_warehouse_sidecars(target_dir: Path, exe_suffix: str, entries: list[dict]) -> list[str]:
    """One "Depot_N.warehouse.json" per entry next to Depot_N.exe - same
    reasoning as write_dealership_sidecars()."""
    written = []
    for i, entry in enumerate(entries, start=1):
        sidecar_path = target_dir / f"Depot_{i}{WAREHOUSE_SIDECAR_SUFFIX}"
        sidecar_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(sidecar_path.name)
    return written

def find_payload_exe(payload: Path, prefix: str) -> Path:
    candidate = payload / f"{prefix}.exe"
    if candidate.exists():
        return candidate
    # Non-Windows dev builds of the payload (rare, but build_setup.py can
    # technically run on any platform) won't have a .exe suffix.
    candidate = payload / prefix
    if candidate.exists():
        return candidate
    print(
        f"ERROR: expected bundled application {prefix!r} not found in the\n"
        f"embedded payload ({payload}). This setup.exe may be corrupted or\n"
        f"was built incorrectly - re-run build_setup.py and try again."
    )
    pause_and_exit(1)
    raise AssertionError("unreachable")  # keeps type-checkers happy


def main() -> None:
    print("=== POS / Inventory System - Setup ===\n")
    print(
        "This will install the applications you choose into the CURRENT\n"
        f"FOLDER:\n    {Path.cwd()}\n"
    )

    payload = payload_dir()

    counts: dict[str, int] = {}
    for key, label, _prefix in ROLES:
        counts[key] = prompt_int(f"How many {label} instances do you want to install here?")

    if sum(counts.values()) == 0:
        print("\nNo instances requested - nothing to install.")
        pause_and_exit(0)

    db_path = prompt_db_path()

    print("\nAbout to install:")
    for key, label, _prefix in ROLES:
        if counts[key]:
            print(f"  {label}: {counts[key]}")
    print(f"  Shared database path: {db_path if db_path else '(each app will pick its own default)'}")
    print(f"  Destination folder: {Path.cwd()}\n")

    confirm = input("Proceed? [Y/n] ").strip().lower()
    if confirm not in ("", "y", "yes"):
        print("Cancelled - nothing was installed.")
        pause_and_exit(0)

    target_dir = Path.cwd()
    written: list[str] = []

    for key, label, prefix in ROLES:
        count = counts[key]
        if count == 0:
            continue
        src_exe = find_payload_exe(payload, prefix)
        for i in range(1, count + 1):
            dest_name = f"{prefix}_{i}{src_exe.suffix}"
            dest_path = target_dir / dest_name
            shutil.copy2(src_exe, dest_path)
            written.append(dest_name)
        print(f"  Installed {count}x {label} -> {', '.join(written[-count:])}")

        if key == "pos":
            entries = prompt_dealership_details(count)
            sidecars = write_dealership_sidecars(target_dir, src_exe.suffix, entries)
            written.extend(sidecars)
            print(f"  Registered {count}x dealership sidecar -> {', '.join(sidecars)}")
        elif key == "depot":
            entries = prompt_warehouse_details(count)
            sidecars = write_warehouse_sidecars(target_dir, src_exe.suffix, entries)
            written.extend(sidecars)
            print(f"  Registered {count}x warehouse sidecar -> {', '.join(sidecars)}")

    config: dict[str, str] = {}
    if db_path:
        config["db_path"] = db_path
    config_path = target_dir / CONFIG_FILENAME
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    written.append(config_path.name)

    print(f"\nDone. {target_dir} now contains:")
    for name in sorted(written):
        print(f"  {name}")
    print(
        "\nEach .exe reads config.json from its OWN folder at startup, so\n"
        "keep config.json together with the .exe files you just installed -\n"
        "if you move one of them (e.g. a POS_2.exe) to a different folder\n"
        "or machine, take a copy of config.json along with it."
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

    pause_and_exit(0)


if __name__ == "__main__":
    main()
