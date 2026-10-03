# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Depot app. Mirrors pos_app/pos_app.spec and
# admin_app/admin_app.spec - see pos_app's header comment for the
# rationale behind pathex/datas.
#
# Build via depot_app\build_exe.bat, or manually from the repository root:
#   pyinstaller depot_app\depot_app.spec --distpath depot_app\dist --workpath depot_app\build --noconfirm

import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"  # UPX only on Windows; it misbehaves on macOS

REPO_ROOT = Path(SPECPATH).parent  # depot_app/ -> repo root
sys.path.insert(0, str(REPO_ROOT))

from shared.build_manifest import BUNDLED_DATA_FILES  # single source of truth - see that file

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "depot_app" / "main.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[(str(REPO_ROOT / rel_path), dest) for rel_path, dest in BUNDLED_DATA_FILES],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DepotApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=IS_WINDOWS,
    console=False,
    icon=None,  # TODO: set to a .ico path once branding assets exist
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=IS_WINDOWS,
    upx_exclude=[],
    name="DepotApp",
)

# macOS only: wrap the onedir output into a double-clickable .app bundle
# (Windows keeps the plain folder + .exe). Unsigned - see packaging/README.md
# for the one-time "open it anyway" step on a Mac.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="GPUSA Depot.app",
        icon=None,  # TODO: set to a .icns path once branding assets exist
        bundle_identifier="com.gpusa.depot",
        info_plist={
            "CFBundleDisplayName": "GPUSA Depot",
            "NSHighResolutionCapable": True,
        },
    )
