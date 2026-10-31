"""PostgreSQL connections (psycopg 3)."""

from __future__ import annotations

import psycopg
from psycopg.conninfo import make_conninfo

from .aws_secrets import fetch_db_credentials
from .config import Settings


class DatabaseNotConfigured(RuntimeError):
    pass


def conninfo_for(settings: Settings) -> str:
    """Build the libpq connection string. Hits Secrets Manager only in AWS mode."""
    if settings.mode == "url":
        return settings.database_url  # type: ignore[return-value]
    if settings.mode == "aws":
        creds = fetch_db_credentials(settings.db_secret_arn)  # type: ignore[arg-type]
        return make_conninfo(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=creds.username,
            password=creds.password,
            sslmode=settings.db_sslmode,
            connect_timeout=5,
            application_name="gpusa-server",
        )
    raise DatabaseNotConfigured("Set DATABASE_URL (local) or GPUSA_DB_HOST/NAME/SECRET_ARN (AWS)")


def connect(settings: Settings) -> psycopg.Connection:
    return psycopg.connect(conninfo_for(settings))


def ping(settings: Settings) -> None:
    """Raise if the database can't answer ``SELECT 1``."""
    with connect(settings) as conn:
        conn.execute("SELECT 1").fetchone()
