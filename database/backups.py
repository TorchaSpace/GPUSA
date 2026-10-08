"""Automatic daily backups of the shared database.

Every app calls daily_backup() when it opens and once an hour while it
runs, so a till left open for weeks still produces one copy a day. The
first call on a given day writes `backups/shared_backend-YYYY-MM-DD.db`
beside the database (SQLite's online backup via copy_database_to, so the
open apps and the WAL file are no problem); every later call that day is
a cheap "already there". The newest DAILY_BACKUPS_KEPT copies are kept,
older ones deleted.

Three apps opening at once must not trip over each other: each writes to
its own temporary name and renames it into place, so a half-written file
never carries the final name. A backup is a safety net, never a reason for
an app not to open - every failure is swallowed and reported as None.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date
from pathlib import Path

from database.connection import copy_database_to
from database.exceptions import DataAccessError
from shared.paths import get_db_path

DAILY_BACKUPS_KEPT = 14
BACKUP_DIR_NAME = "backups"


def backup_dir() -> Path:
    return Path(get_db_path()).parent / BACKUP_DIR_NAME


def _name_for(db_path: Path, day: date) -> str:
    return f"{db_path.stem}-{day:%Y-%m-%d}{db_path.suffix}"


def _dated_backups(folder: Path, db_path: Path) -> list[Path]:
    pattern = re.compile(re.escape(db_path.stem) + r"-\d{4}-\d{2}-\d{2}" + re.escape(db_path.suffix) + "$")
    return sorted(p for p in folder.iterdir() if pattern.match(p.name))  # ISO dates sort by name


def daily_backup(today: date | None = None, keep: int = DAILY_BACKUPS_KEPT) -> Path | None:
    """Make today's backup if it isn't there yet and prune old ones.
    Returns today's backup path, or None if it could not be written."""
    try:
        db_path = Path(get_db_path())
        folder = backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / _name_for(db_path, today or date.today())
        if not target.exists():
            temporary = folder / f".{target.name}.{os.getpid()}.tmp"
            temporary.unlink(missing_ok=True)
            copy_database_to(temporary)
            os.replace(temporary, target)
        for old in _dated_backups(folder, db_path)[:-keep] if keep > 0 else []:
            old.unlink(missing_ok=True)
        return target
    except (OSError, sqlite3.Error, DataAccessError):
        return None
