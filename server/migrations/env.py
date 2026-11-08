"""Alembic environment: connects exactly like the API does (config + db)."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# server/ on the path so ``gpusa_server`` imports when alembic runs from there.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpusa_server import db  # noqa: E402
from gpusa_server.config import load_settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Raw-SQL migrations: no SQLAlchemy models, so no autogenerate.
target_metadata = None


def run_migrations_online() -> None:
    settings = config.attributes.get("settings") or load_settings()  # the API passes its own when it self-migrates
    # The URL is a placeholder; ``creator`` hands SQLAlchemy a psycopg 3
    # connection built by the same code the API uses (including the AWS
    # Secrets Manager password lookup).
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: db.connect(settings),
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) mode is not supported; run against a database.")
run_migrations_online()
