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
    upx=True,
    console=False,
    icon=None,  # TODO: set to a .ico path once branding assets exist
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DepotApp",
)
