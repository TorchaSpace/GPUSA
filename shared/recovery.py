"""Getting back in when the administrator PIN is forgotten.

There is deliberately no "forgot PIN" button that works on its own - that
would let anyone standing at the screen take over Admin. Instead, resetting
needs proof of control over the computer: an empty file with a fixed name
must be created in the data folder (the folder that holds the shared
database), which only someone with file access can do. The reset also
leaves a line in Settings > Sign-in activity.

Pure functions - no Qt, no database - so the rule is testable alone.
"""

from __future__ import annotations

from pathlib import Path

RECOVERY_FILENAME = "RESET_ADMIN_ACCESS.txt"


def recovery_flag_path(db_path: Path) -> Path:
    """Where the proof file must be created: beside the shared database."""
    return Path(db_path).parent / RECOVERY_FILENAME


def is_armed(flag_path: Path) -> bool:
    """True when the proof file exists (a regular file, not a folder)."""
    return Path(flag_path).is_file()


def disarm(flag_path: Path) -> None:
    """Remove the proof file after a successful reset so it can't be reused."""
    try:
        Path(flag_path).unlink()
    except FileNotFoundError:
        pass
