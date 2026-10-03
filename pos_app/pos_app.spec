# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the Branch POS app. Bundles pos_app/main.py plus
# the sibling `shared` and `database` packages it depends on (they live
# outside pos_app/, so `pathex` below is what makes them importable in
# the frozen build).
#
# Build via pos_app\build_exe.bat (from anywhere), or manually from the
# repository root:
#   pyinstaller pos_app\pos_app.spec --distpath pos_app\dist --workpath pos_app\build --noconfirm

import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).parent  # pos_app/ -> repo root
sys.path.insert(0, str(REPO_ROOT))  # so the import below resolves regardless of CWD

from shared.build_manifest import BUNDLED_DATA_FILES  # single source of truth - see that file

block_cipher = None

a = Analysis(
    [str(REPO_ROOT / "pos_app" / "main.py")],
    pathex=[str(REPO_ROOT)],  # so `import shared`, `import database` resolve
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
    name="BranchPOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed GUI app - no console window behind it
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
    name="BranchPOS",
)
