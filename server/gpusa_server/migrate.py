"""Bring the database schema up to date when the server starts (``alembic upgrade head``).

Saves running alembic by hand after every update: pull the new code, restart the server, done. Set
``GPUSA_AUTO_MIGRATE=0`` to turn it off (for example where the schema is managed separately). A failed
migration stops the server from starting, with the reason in the log, rather than running on a schema the
code does not match.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("gpusa_server")

SERVER_DIR = Path(__file__).resolve().parent.parent


def enabled(settings) -> bool:
    if (os.environ.get("GPUSA_AUTO_MIGRATE") or "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return settings.mode != "none"  # no database configured: nothing to migrate


def upgrade_to_head(settings) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    config.attributes["settings"] = settings
    log.info("Checking the database schema (alembic upgrade head)")
    command.upgrade(config, "head")
