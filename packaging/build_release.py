#!/usr/bin/env python3
"""Build the downloadable release for the OS this runs on.

    python packaging/build_release.py            # -> release/

PyInstaller cannot cross-compile: a Windows build has to be made on
Windows and a Mac build on a Mac. The GitHub Actions workflow
(.github/workflows/build.yml) runs this once on each, so nobody has to own
both machines; run it by hand to make a build for the machine you're on.

Output, in release/ (names are stable so ".../releases/latest/download/<name>"
links keep working release after release):

    Windows   GPUSA-Windows-x64.zip       three app folders + INSTALL.txt
    macOS     GPUSA-macOS-<arch>.dmg      three .app bundles to drag into Applications
              GPUSA-macOS-<arch>.zip      the same, for anyone who prefers a zip

Each app is built with its own onedir .spec (pos_app/pos_app.spec etc. -
the same files build_exe.bat uses), so what ships here is exactly what the
Windows installers wrap.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_ROOT = REPO_ROOT / "build_release"
RELEASE_DIR = REPO_ROOT / "release"


@dataclass(frozen=True)
class App:
    key: str  # folder + spec name: pos_app, depot_app, admin_app
    exe_name: str  # the name inside the spec (COLLECT / EXE)
    display: str  # what the user sees: "GPUSA POS"


APPS = (
    App("admin_app", "AdminDashboard", "GPUSA Admin"),
    App("pos_app", "BranchPOS", "GPUSA POS"),
    App("depot_app", "DepotApp", "GPUSA Depot"),
)


def machine_label() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(str(part) for part in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def build_app(app: App) -> Path:
    """Run PyInstaller on the app's spec and return what it produced: the
    folder holding the .exe (Windows) or the .app bundle (macOS)."""
    dist = BUILD_ROOT / app.key / "dist"
    work = BUILD_ROOT / app.key / "work"
    run(
        [sys.executable, "-m", "PyInstaller", str(REPO_ROOT / app.key / f"{app.key}.spec"),
         "--distpath", str(dist), "--workpath", str(work), "--noconfirm", "--clean"],
        cwd=REPO_ROOT,
    )
    produced = dist / f"{app.display}.app" if sys.platform == "darwin" else dist / app.exe_name
    if not produced.exists():
        raise SystemExit(f"PyInstaller finished but {produced} is missing - check its output above.")
    return produced


INSTALL_WINDOWS = """GPUSA - Windows

1. Unzip this folder anywhere you like (for example C:\\GPUSA). Don't run the
   apps from inside the zip.
2. Open "GPUSA Admin" first and double-click AdminDashboard.exe. On a new
   system it asks you to create the first administrator.
3. Then open the others the same way: GPUSA POS\\BranchPOS.exe at a till,
   GPUSA Depot\\DepotApp.exe in the warehouse.

All three apps on one computer share one database, kept in
C:\\ProgramData\\POSInventorySystem. To point apps on several computers at one
shared database, put a config.json next to each .exe:
    {"db_path": "\\\\\\\\server\\\\share\\\\shared_backend.db"}

The first time Windows may say "Windows protected your PC" because the apps
aren't code-signed yet: click "More info", then "Run anyway".
"""

INSTALL_MACOS = """GPUSA - macOS

1. Drag GPUSA Admin, GPUSA POS and GPUSA Depot into the Applications folder
   (or any folder you like).
2. Open GPUSA Admin first. On a new system it asks you to create the first
   administrator. Then open the others as you need them.

All three apps on one Mac share one database, kept in
~/Library/Application Support/POSInventorySystem. To point apps on several
Macs at one shared database, put a config.json in the folder that holds the
app (next to GPUSA POS.app):
    {"db_path": "/Volumes/share/shared_backend.db"}

The first time macOS may say the app "can't be opened" or "is damaged",
because the apps aren't signed with an Apple developer certificate yet. This
is expected, and the app is not damaged. Either:
  - right-click (or Control-click) the app, choose Open, then Open again; or
  - run this once in Terminal:
        xattr -dr com.apple.quarantine "/Applications/GPUSA Admin.app"
        xattr -dr com.apple.quarantine "/Applications/GPUSA POS.app"
        xattr -dr com.apple.quarantine "/Applications/GPUSA Depot.app"
"""


def package_windows(built: dict[App, Path]) -> list[Path]:
    stage = BUILD_ROOT / "stage" / "GPUSA"
    shutil.rmtree(stage.parent, ignore_errors=True)
    for app, folder in built.items():
        shutil.copytree(folder, stage / app.display)
    (stage / "INSTALL.txt").write_text(INSTALL_WINDOWS, encoding="utf-8")
    archive = RELEASE_DIR / f"GPUSA-Windows-{machine_label()}"
    shutil.make_archive(str(archive), "zip", root_dir=stage.parent, base_dir=stage.name)
    return [archive.with_suffix(".zip")]


def package_macos(built: dict[App, Path]) -> list[Path]:
    label = machine_label()
    stage = BUILD_ROOT / "stage" / "GPUSA"
    shutil.rmtree(stage.parent, ignore_errors=True)
    stage.mkdir(parents=True)
    for app, bundle in built.items():
        target = stage / bundle.name
        # ditto, not shutil: an .app is full of symlinks that must survive.
        run(["ditto", str(bundle), str(target)])
        # Apple Silicon refuses unsigned code outright; an ad-hoc signature
        # ("-") is free and is enough for the right-click > Open route.
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(target)], check=False)
    (stage / "INSTALL.txt").write_text(INSTALL_MACOS, encoding="utf-8")
    (stage / "Applications").symlink_to("/Applications")

    dmg = RELEASE_DIR / f"GPUSA-macOS-{label}.dmg"
    dmg.unlink(missing_ok=True)
    run(["hdiutil", "create", "-volname", "GPUSA", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(dmg)])
    zipped = RELEASE_DIR / f"GPUSA-macOS-{label}.zip"
    zipped.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--keepParent", str(stage), str(zipped)])
    return [dmg, zipped]


def main() -> int:
    if sys.platform not in ("win32", "darwin"):
        print("This builds the Windows or macOS release; run it on one of those (or let CI do it).")
        return 1
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ.pop("QT_QPA_PLATFORM")  # a leftover from running tests must not reach PyInstaller's hooks
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    RELEASE_DIR.mkdir(exist_ok=True)
    built = {app: build_app(app) for app in APPS}
    files = package_windows(built) if sys.platform == "win32" else package_macos(built)
    print("\nBuilt:")
    for path in files:
        print(f"  {path}  ({path.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
