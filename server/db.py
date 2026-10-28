"""PostgreSQL connections for the server.

With IAM auth (the default) the "password" is a signed token that AWS issues
for 15 minutes; it is minted again on every new connection, so no database
password exists anywhere to leak. The token helper is injectable so tests
never need AWS.
"""

from __future__ import annotations

import time
from typing import Callable

import psycopg
from psycopg_pool import ConnectionPool

from server.config import Settings

TokenFactory = Callable[[Settings], str]

# Tokens last 15 minutes; reuse one for a while instead of calling AWS per connection.
_TOKEN_TTL_SECONDS = 600
_token_cache: dict[tuple[str, int, str, str], tuple[float, str]] = {}


def iam_token(settings: Settings) -> str:
    """A cached RDS/Aurora IAM auth token for settings' host/user."""
    key = (settings.host, settings.port, settings.user, settings.region)
    now = time.monotonic()
    cached = _token_cache.get(key)
    if cached and now - cached[0] < _TOKEN_TTL_SECONDS:
        return cached[1]
    import boto3  # imported lazily: only IAM mode (and not the tests) needs it

    client = boto3.client("rds", region_name=settings.region)
    token = client.generate_db_auth_token(
        DBHostname=settings.host,
        Port=settings.port,
        DBUsername=settings.user,
        Region=settings.region,
    )
    _token_cache[key] = (now, token)
    return token


def connect_kwargs(settings: Settings, token_factory: TokenFactory = iam_token) -> dict:
    """Keyword arguments for psycopg.connect, with the right password source."""
    if settings.database_url:
        return {"conninfo": settings.database_url}
    password = token_factory(settings) if settings.uses_iam else settings.password
    return {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.name,
        "user": settings.user,
        "password": password,
        "sslmode": settings.sslmode,
        "connect_timeout": 30,  # a paused Aurora cluster takes ~15 s to resume
    }


def connect(settings: Settings, token_factory: TokenFactory = iam_token) -> psycopg.Connection:
    return psycopg.connect(**connect_kwargs(settings, token_factory))


def make_pool(settings: Settings, token_factory: TokenFactory = iam_token) -> ConnectionPool:
    """A small pool whose new connections each get a fresh/valid IAM token."""

    class _Connection(psycopg.Connection):
        @classmethod
        def connect(cls, conninfo="", **kwargs):  # type: ignore[override]
            kwargs.update(connect_kwargs(settings, token_factory))
            return super().connect(kwargs.pop("conninfo", ""), **kwargs)

    return ConnectionPool(
        conninfo="",
        connection_class=_Connection,
        min_size=1,
        max_size=5,
        max_lifetime=_TOKEN_TTL_SECONDS,
        open=False,
    )
