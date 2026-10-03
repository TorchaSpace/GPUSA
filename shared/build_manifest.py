"""Single source of truth for what gets bundled into every packaged build.

Two independent build paths read this file: the per-app onedir
.spec files (pos_app.spec, depot_app.spec, admin_app.spec - used by
build_exe.bat, feeding the Inno Setup installers) and deploy_system.py
(onefile mass-deployment). Without this file, each would need its own
hardcoded `datas` list, and the two could silently drift apart the next
time a bundled resource is added. Add it here once; both build paths
pick it up automatically.

Deliberately plain stdlib-only (no PySide6/Qt import) - PyInstaller's
.spec files execute this at spec-parse time, before Analysis has run,
so anything with heavier dependencies here would slow down or risk
breaking spec parsing itself.

Each entry is (path relative to repo root, destination folder inside the
bundle) - the same shape shared.paths.resource_path() expects callers
(e.g. database/connection.py's _SCHEMA_PATH) to resolve at runtime.
"""

from __future__ import annotations

BUNDLED_DATA_FILES: list[tuple[str, str]] = [
    ("database/schema.sql", "database"),
    # TTFs the PDF export embeds (Helvetica cannot draw Turkish letters).
    ("shared/assets/fonts/DejaVuSans.ttf", "shared/assets/fonts"),
    ("shared/assets/fonts/DejaVuSans-Bold.ttf", "shared/assets/fonts"),
    ("shared/assets/fonts/LICENSE-DejaVu.txt", "shared/assets/fonts"),
]
