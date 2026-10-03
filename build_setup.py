#!/usr/bin/env python3
"""Stage 1 Master Builder - produces the single setup.exe handed to branch managers.

THIS SCRIPT IS FOR THE DEVELOPER. Run it straight from the GPUSA repo root:

    python build_setup.py

or, once you've compiled it into build.exe (see make_build_exe.bat - a
one-time step, redone whenever this file changes), just double-click
build.exe sitting there instead. Either way does exactly the same thing -
build.exe is a convenience wrapper around this same script, nothing more.
It is NOT a standalone tool: it still needs to be run from inside a full
GPUSA checkout, with a `python` on PATH, because it shells out to
`python -m PyInstaller` to compile `admin_app/main.py` etc., and those
source files have to actually be on disk next to it. (Contrast with this
script's own OUTPUT, setup.exe, which - by design - needs none of that
on the machine that runs it; see installer.py's docstring.) It does NOT,
however, require you to have already run `pip install -r
requirements.txt` yourself: if PySide6 or PyInstaller aren't importable
from that `python`, check_environment() below tries installing
requirements.txt automatically before giving up - see
_auto_install_dependencies().

It does the heavy lifting so nobody downstream ever needs Python,
PyInstaller, or this source tree at all:

    1. Builds admin_app/main.py, pos_app/main.py, and depot_app/main.py
       each into a standalone, onefile, --noconsole .exe (same approach
       as deploy_system.py's per-role build, reusing the same
       shared/build_manifest.BUNDLED_DATA_FILES manifest so neither
       script can drift from what actually needs to be bundled).
    2. Builds installer.py - the dependency-free interactive wizard in
       this same repo - into its own onefile .exe named setup.exe.
       Unlike the three application builds, this one is built WITHOUT
       --noconsole: installer.py is a text wizard that reads/writes the
       console, and --noconsole would hide that console window entirely,
       making the wizard silently unusable. This is intentional, not a
       copy-paste oversight - see build_setup_exe() below.
    3. Bundles the three already-built application .exe files INSIDE
       setup.exe using PyInstaller's --add-data, each landing under a
       "payload/" folder inside the bundle. installer.py finds them
       there at runtime via sys._MEIPASS (see installer.py's
       payload_dir() for the extraction-side half of this handshake).

The end result, in Kurulum/setup.exe, is a SINGLE FILE that already
contains everything needed to install any combination of Admin/POS/Depot
instances on a machine with no Python and no network access required -
that's the whole point of a two-stage build: all of PyInstaller's and
this repo's complexity stays on the developer's machine, and what ships
is one ordinary-looking .exe.

THIS IS A THIRD, SEPARATE BUILD PATH alongside the per-app onedir .spec
files (pos_app/build_exe.bat + Inno Setup, for a traditional one-app-per-
machine installer) and deploy_system.py (a flat onefile mass-copy, for
the developer's own quick local testing - see its own module docstring).
None of the three replace each other; this one is the recommended path
for handing a single file to someone outside the dev team to run
unattended installs with.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Frozen-aware, the same idea as shared/paths.py's exe-adjacent config
# lookup: __file__ is meaningless once this script is compiled into
# build.exe (PyInstaller resolves it to somewhere inside the frozen
# bundle, not build.exe's real location on disk), so when frozen, use
# sys.executable's own folder instead - i.e. wherever build.exe was
# double-clicked from. That's why make_build_exe.bat builds build.exe
# straight into the repo root: this only works correctly if build.exe
# actually sits next to admin_app/, pos_app/, depot_app/, shared/,
# database/, and installer.py, exactly where build_setup.py itself
# would need to be run from.
if getattr(sys, "frozen", False):
    REPO_ROOT = Path(sys.executable).resolve().parent
else:
    REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))  # so `import shared` resolves regardless of CWD

from shared.build_manifest import BUNDLED_DATA_FILES  # noqa: E402

BUILD_DIR = REPO_ROOT / "_setup_build"          # scratch space - wiped on success
PAYLOAD_DIR = BUILD_DIR / "payload"             # the 3 built app exes land here
SETUP_DIST_DIR = REPO_ROOT / "Kurulum"          # final setup.exe lands here
INSTALLER_SRC = REPO_ROOT / "installer.py"
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
REQUIRED_PACKAGES = ("PySide6", "PyInstaller")  # the two this script itself needs at build time


@dataclass(frozen=True)
class PayloadRole:
    entry_script: str  # relative to REPO_ROOT
    name: str           # PyInstaller --name, and the payload/<name>.exe filename -
                         # MUST match one of installer.py's ROLES prefixes exactly.


# Must exactly match installer.py's ROLES (key, label, prefix) - the `name`
# here is that same prefix.
PAYLOAD_ROLES = [
    PayloadRole("admin_app/main.py", "Admin"),
    PayloadRole("pos_app/main.py", "POS"),
    PayloadRole("depot_app/main.py", "Depot"),
]


def _python_executable() -> str:
    """The interpreter used for every `-m pip` / `-m PyInstaller` subprocess call.

    When NOT frozen, sys.executable IS this script's own interpreter -
    the active venv's python.exe, the same one `pip install -r
    requirements.txt` went into - so using it guarantees every subprocess
    call below uses that exact interpreter (see pos_app/build_exe.bat's
    header comment for why that matters: a bare `pyinstaller` on PATH can
    silently belong to a DIFFERENT Python and produce a build that
    "succeeds" but crashes at launch with `No module named 'PySide6'`).

    When frozen (running as build.exe), sys.executable is build.exe
    ITSELF, not a Python interpreter - using it here would try to run
    `build.exe -m pip` / `build.exe -m PyInstaller`, which fails outright.
    A frozen build.exe has no way to know which python.exe its own venv
    used, so it falls back to a bare `python` on PATH - correct as long as
    build.exe is launched from the same activated venv shell
    `python build_setup.py` would have used.
    """
    return "python" if getattr(sys, "frozen", False) else sys.executable


def _pyinstaller_invocation() -> list[str]:
    return [_python_executable(), "-m", "PyInstaller"]


def _missing_packages() -> list[str] | None:
    """Which of REQUIRED_PACKAGES the active `python` can't import.

    Returns None (rather than a list) if `python` itself couldn't be run
    at all - a different, worse problem than a missing package, and one
    _auto_install_dependencies() can't fix by installing anything.

    When NOT frozen, this process IS the interpreter in question, so it
    checks itself directly with __import__(). When frozen (running as
    build.exe), __import__() would only see what's bundled INSIDE
    build.exe's own frozen bundle - not what the ambient `python` on PATH
    has installed, which is what actually matters, since every real
    PyInstaller invocation is delegated to that external interpreter (see
    _python_executable()). So the frozen case shells out to
    `python -c "import ..."` instead - the same check
    pos_app/build_exe.bat runs before building.
    """
    if not getattr(sys, "frozen", False):
        missing = []
        for module_name in REQUIRED_PACKAGES:
            try:
                __import__(module_name)
            except ImportError:
                missing.append(module_name)
        return missing

    missing = []
    for module_name in REQUIRED_PACKAGES:
        try:
            result = subprocess.run(
                ["python", "-c", f"import {module_name}"],
                capture_output=True,
            )
        except OSError:
            # No `python` on PATH at all - can't check package-by-package,
            # and definitely can't auto-install anything without it.
            return None
        if result.returncode != 0:
            missing.append(module_name)
    return missing


def _auto_install_dependencies() -> bool:
    """Try `python -m pip install -r requirements.txt` once. True on success.

    This is what lets check_environment() below recover from a missing
    PySide6/PyInstaller automatically instead of just telling you to fix
    it yourself - reinstalling the whole requirements.txt (not just the
    specific package(s) found missing) so the result matches exactly what
    a fresh `pip install -r requirements.txt` would have given you, the
    same file every other setup step in this project already depends on.
    Needs network access to PyPI; a failure here (offline, a locked-down
    proxy, etc.) is reported and check_environment() falls through to its
    normal "fix this yourself" error.
    """
    if not REQUIREMENTS_FILE.exists():
        print(f"  (no requirements.txt found at {REQUIREMENTS_FILE} - can't auto-install)")
        return False

    print(
        f"\nMissing dependencies detected - attempting to install them automatically:\n"
        f"    {_python_executable()} -m pip install -r {REQUIREMENTS_FILE}\n"
    )
    try:
        result = subprocess.run(
            [_python_executable(), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)]
        )
    except OSError as exc:
        print(f"  Could not run pip: {exc}")
        return False

    if result.returncode != 0:
        print(f"\n  pip install failed (exit {result.returncode}) - see output above.")
        return False
    return True


def check_environment() -> None:
    """Fail fast, before spending minutes in PyInstaller x4, if this
    interpreter can't build these apps or installer.py is missing.

    If PySide6 and/or PyInstaller are missing, this tries ONE automatic
    `pip install -r requirements.txt` (see _auto_install_dependencies())
    before giving up - so the common case (a fresh checkout, or a venv
    that's fallen behind requirements.txt) is just "run build.exe again
    in a moment," not a manual `pip install` round-trip. It only ever
    fixes the *active* `python`'s packages, never creates or activates a
    venv for you, and it does nothing at all if `python` itself isn't on
    PATH.
    """
    missing = _missing_packages()
    if missing is None:
        print(
            "ERROR: no `python` found on PATH.\n"
            "Install Python (or activate the venv you built this project's\n"
            "dependencies into) in THIS terminal, then re-run build.exe\n"
            "(or `python build_setup.py`)."
        )
        sys.exit(1)

    if missing:
        print("Missing packages detected: " + ", ".join(missing))
        if _auto_install_dependencies():
            missing = _missing_packages() or []  # re-check post-install; None can't recur here

    if missing:
        print(
            "\nERROR: the active `python` is still missing: " + ", ".join(missing) + "\n"
            "Automatic install didn't resolve it - activate the venv you ran\n"
            "`pip install -r requirements.txt` into in THIS terminal (or check\n"
            "your network/proxy access to PyPI), then re-run build.exe\n"
            "(or `python build_setup.py`)."
        )
        sys.exit(1)

    if not INSTALLER_SRC.exists():
        print(f"ERROR: {INSTALLER_SRC} not found - nothing to compile setup.exe from.")
        sys.exit(1)

    if sys.platform != "win32":
        print(
            "WARNING: this project's deployment targets Windows (.exe,\n"
            "%ProgramData%, Inno Setup). PyInstaller cannot cross-compile -\n"
            "anything built here will be for THIS platform, not Windows.\n"
        )


def run_pyinstaller(cmd: list[str], label: str) -> None:
    print(f"\nBuilding {label} ...")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        print(
            f"\nERROR: PyInstaller failed while building {label} "
            f"(exit {result.returncode}). Stopping - build cache left in "
            f"{BUILD_DIR} for inspection."
        )
        sys.exit(result.returncode)


def build_payload_app(role: PayloadRole) -> Path:
    """Build one application as a onefile, --noconsole .exe into PAYLOAD_DIR.

    See _pyinstaller_invocation() for why the PyInstaller command prefix
    isn't simply `sys.executable -m PyInstaller` once this script can
    itself be frozen into build.exe.
    """
    entry_path = REPO_ROOT / role.entry_script
    work_dir = BUILD_DIR / "work" / role.name
    spec_dir = BUILD_DIR / "spec" / role.name

    cmd = [
        *_pyinstaller_invocation(),
        str(entry_path),
        "--onefile",
        "--noconsole",
        "--name", role.name,
        "--distpath", str(PAYLOAD_DIR),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--paths", str(REPO_ROOT),
        "--noconfirm",
        "--clean",
    ]
    for rel_path, dest in BUNDLED_DATA_FILES:
        cmd += ["--add-data", f"{REPO_ROOT / rel_path}{os.pathsep}{dest}"]

    run_pyinstaller(cmd, f"{role.name} ({role.entry_script})")

    built_exe = PAYLOAD_DIR / f"{role.name}.exe"
    if not built_exe.exists():
        built_exe = PAYLOAD_DIR / role.name  # non-Windows build machine
    if not built_exe.exists():
        print(f"ERROR: expected build output not found at {built_exe}")
        sys.exit(1)
    return built_exe


def build_setup_exe(payload_exes: list[Path]) -> Path:
    """Compile installer.py into setup.exe, embedding each payload exe.

    Deliberately WITHOUT --noconsole: installer.py is an interactive text
    wizard (input()/print()) - a --noconsole build would run with no
    console window at all, so the person double-clicking it would see
    nothing happen and never be able to answer its prompts. Every other
    build in this project uses --noconsole because those are PySide6 GUI
    apps that draw their own window; this one is not, so it doesn't.
    """
    work_dir = BUILD_DIR / "work" / "setup"
    spec_dir = BUILD_DIR / "spec" / "setup"

    cmd = [
        *_pyinstaller_invocation(),
        str(INSTALLER_SRC),
        "--onefile",
        "--name", "setup",
        "--distpath", str(SETUP_DIST_DIR),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--noconfirm",
        "--clean",
    ]
    # Every payload exe is added to the SAME "payload" destination folder
    # inside the bundle (their source basenames differ - Admin.exe,
    # POS.exe, Depot.exe - so there's no collision); installer.py's
    # payload_dir() reads them all back out of sys._MEIPASS/payload.
    for exe_path in payload_exes:
        cmd += ["--add-data", f"{exe_path}{os.pathsep}payload"]

    run_pyinstaller(cmd, "setup.exe (installer.py)")

    built_exe = SETUP_DIST_DIR / "setup.exe"
    if not built_exe.exists():
        built_exe = SETUP_DIST_DIR / "setup"  # non-Windows build machine
    if not built_exe.exists():
        print(f"ERROR: expected build output not found at {built_exe}")
        sys.exit(1)
    return built_exe


def main() -> None:
    print("=== POS / Inventory System - Stage 1: Master Builder ===\n")
    check_environment()

    for d in (BUILD_DIR, SETUP_DIST_DIR):
        if d.exists():
            shutil.rmtree(d)
    PAYLOAD_DIR.mkdir(parents=True)
    SETUP_DIST_DIR.mkdir(parents=True)

    print("Step 1/2: building the 3 application payloads ...")
    payload_exes = [build_payload_app(role) for role in PAYLOAD_ROLES]

    print("\nStep 2/2: building setup.exe and embedding the payloads ...")
    setup_exe = build_setup_exe(payload_exes)

    print("\nCleaning up build cache ...")
    shutil.rmtree(BUILD_DIR, ignore_errors=True)

    size_mb = setup_exe.stat().st_size / (1024 * 1024)
    print(
        f"\nDone. {setup_exe} ({size_mb:.1f} MB) is ready.\n\n"
        "Hand this ONE file to a branch manager. They double-click it in\n"
        "an empty folder, answer a few prompts (how many Admin/POS/Depot\n"
        "instances, and the shared database path), and it installs\n"
        "everything into that folder - no Python, no installer, no other\n"
        "files required.\n"
    )


def _run_and_pause_if_frozen() -> None:
    """Run main(), then (only when frozen, i.e. running as build.exe) hold
    the console open until the developer acknowledges the result.

    A double-clicked build.exe's console window closes the instant the
    process exits, same issue as installer.py/setup.exe - without this,
    a build failure's error text (or the final success summary) would
    flash and vanish. Plain `python build_setup.py` from a terminal has
    no such problem (the terminal itself doesn't close), so this adds no
    extra prompt in that case - exit codes and behavior are unchanged.

    main() and its helpers raise SystemExit directly (sys.exit(...)) on
    every failure path; catching it here, once, means every one of those
    call sites gets the pause for free without threading a pause call
    through each of them individually.
    """
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1

    if getattr(sys, "frozen", False):
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass

    sys.exit(code)


if __name__ == "__main__":
    _run_and_pause_if_frozen()
