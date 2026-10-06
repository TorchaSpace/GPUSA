#!/usr/bin/env python3
"""Build the downloadable release for the OS this runs on.

    python packaging/build_release.py            # -> release/

PyInstaller cannot cross-compile: a Windows build has to be made on
Windows, a Mac build on a Mac and a Linux build on Linux. The GitHub Actions workflow
(.github/workflows/build.yml) runs this once on each, so nobody has to own
both machines; run it by hand to make a build for the machine you're on.

Output, in release/ (names are stable so ".../releases/latest/download/<name>"
links keep working release after release):

    Windows   GPUSA-Windows-x64.zip       three app folders + INSTALL.txt
    macOS     GPUSA-macOS-<arch>.dmg      three .app bundles to drag into Applications
              GPUSA-macOS-<arch>.zip      the same, for anyone who prefers a zip
    Linux     GPUSA-Linux-<arch>.tar.gz   three app folders + install.sh + INSTALL.txt
                                          (built on an older glibc so it runs on Arch,
                                          Ubuntu, Fedora, ... - see build.yml)

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
import time
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
    folder holding the .exe (Windows) or the executable (Linux), or the
    .app bundle (macOS)."""
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


INSTALL_LINUX = """GPUSA - Linux

1. Unpack this archive anywhere you like, e.g. into ~/Apps:
       tar -xzf GPUSA-Linux-x64.tar.gz -C ~/Apps
2. Optional: run ./install.sh once from the unpacked GPUSA folder. It adds
   GPUSA Admin, GPUSA POS and GPUSA Depot to your application menu (only for
   your user - nothing is written outside your home folder). Move the folder
   first if you want it somewhere else; the menu entries point at where it is
   when you run install.sh. ./install.sh --remove takes the entries out again.
3. Open GPUSA Admin first ("GPUSA Admin/AdminDashboard"). On a new system it
   asks you to create the first administrator. Then GPUSA POS/BranchPOS at a
   till and GPUSA Depot/DepotApp in the warehouse.

All three apps run by the same user share one database, kept in
~/.local/share/POSInventorySystem (or $XDG_DATA_HOME/POSInventorySystem).
To point apps on several computers at one shared database, put a config.json
next to each app's executable:
    {"db_path": "/mnt/share/shared_backend.db"}

If an app doesn't start, run it from a terminal to see why. The usual cause
is a missing system library that Qt needs; on Arch Linux:
    sudo pacman -S --needed xcb-util-cursor xcb-util-wm xcb-util-keysyms \\
        xcb-util-image xcb-util-renderutil libxkbcommon-x11 fontconfig
On Wayland the apps run through XWayland by default; QT_QPA_PLATFORM=wayland
also works if qt6-wayland's libraries are present.
"""

# Adds/removes per-user menu entries for the three apps. Written next to them
# in the archive; resolves its own folder so the archive can live anywhere.
INSTALL_SH = """#!/bin/sh
# GPUSA - add (or with --remove, take out) the three apps in your application menu.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS_DIR"
for entry in "admin|GPUSA Admin|AdminDashboard|Manager dashboard" \\
             "pos|GPUSA POS|BranchPOS|Branch point of sale" \\
             "depot|GPUSA Depot|DepotApp|Warehouse floor and console"; do
    key=${entry%%|*}; rest=${entry#*|}
    name=${rest%%|*}; rest=${rest#*|}
    exe=${rest%%|*}; comment=${rest#*|}
    file="$APPS_DIR/gpusa-$key.desktop"
    if [ "${1:-}" = "--remove" ]; then
        rm -f "$file"; echo "removed $file"; continue
    fi
    cat > "$file" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=$comment
Exec="$HERE/$name/$exe"
Path=$HERE/$name
Terminal=false
Categories=Office;Finance;
EOF
    chmod +x "$file"
    echo "added $file"
done
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" || true
"""


def package_linux(built: dict[App, Path]) -> list[Path]:
    stage = BUILD_ROOT / "stage" / "GPUSA"
    shutil.rmtree(stage.parent, ignore_errors=True)
    for app, folder in built.items():
        # symlinks=True: keep PyInstaller's library symlinks as symlinks.
        shutil.copytree(folder, stage / app.display, symlinks=True)
    (stage / "INSTALL.txt").write_text(INSTALL_LINUX, encoding="utf-8")
    script = stage / "install.sh"
    script.write_text(INSTALL_SH, encoding="utf-8", newline="\n")
    script.chmod(0o755)
    # gztar keeps the executable bits (a zip made here would lose them on most unzippers).
    archive = RELEASE_DIR / f"GPUSA-Linux-{machine_label()}"
    shutil.make_archive(str(archive), "gztar", root_dir=stage.parent, base_dir=stage.name)
    return [Path(str(archive) + ".tar.gz")]


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
        signed = subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(target)])
        if signed.returncode != 0:
            print(f"WARNING: ad-hoc signing of {target.name} failed; Apple Silicon may refuse to open it.")
    (stage / "INSTALL.txt").write_text(INSTALL_MACOS, encoding="utf-8")
    (stage / "Applications").symlink_to("/Applications")

    dmg = RELEASE_DIR / f"GPUSA-macOS-{label}.dmg"
    dmg.unlink(missing_ok=True)
    for attempt in range(1, 4):  # "Resource busy" is a known transient hdiutil failure on CI runners
        try:
            run(["hdiutil", "create", "-volname", "GPUSA", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(dmg)])
            break
        except subprocess.CalledProcessError:
            if attempt == 3:
                raise
            print(f"hdiutil failed (attempt {attempt}); retrying...")
            time.sleep(5 * attempt)
    zipped = RELEASE_DIR / f"GPUSA-macOS-{label}.zip"
    zipped.unlink(missing_ok=True)
    run(["ditto", "-c", "-k", "--keepParent", str(stage), str(zipped)])
    return [dmg, zipped]


def main() -> int:
    if not (sys.platform in ("win32", "darwin") or sys.platform.startswith("linux")):
        print("This builds the Windows, macOS or Linux release; run it on one of those (or let CI do it).")
        return 1
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        os.environ.pop("QT_QPA_PLATFORM")  # a leftover from running tests must not reach PyInstaller's hooks
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    RELEASE_DIR.mkdir(exist_ok=True)
    built = {app: build_app(app) for app in APPS}
    if sys.platform == "win32":
        files = package_windows(built)
    elif sys.platform == "darwin":
        files = package_macos(built)
    else:
        files = package_linux(built)
    print("\nBuilt:")
    for path in files:
        print(f"  {path}  ({path.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
