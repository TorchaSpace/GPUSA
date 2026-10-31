"""Server settings, read only from environment variables.

Nothing secret lives in the repo or in an env var on AWS:

* Local dev / tests: ``DATABASE_URL`` (from a git-ignored ``.env``).
* AWS: ``GPUSA_DB_HOST`` + ``GPUSA_DB_NAME`` + ``GPUSA_DB_SECRET_ARN``
  (+ optional ``GPUSA_DB_PORT``, ``GPUSA_DB_SSLMODE``). The username and
  password are fetched at runtime from Secrets Manager.

Setting both modes at once is a configuration error, so a stray local
``DATABASE_URL`` can never silently win over the real database (or the
other way round).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

VALID_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


class ConfigError(ValueError):
    """The environment describes an impossible or incomplete setup."""


def mask_url(url: str) -> str:
    """Hide the password in a ``scheme://user:pass@host/db`` string for logs."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", url)


@dataclass(frozen=True)
class Settings:
    database_url: str | None = None
    db_host: str | None = None
    db_port: int = 5432
    db_name: str | None = None
    db_secret_arn: str | None = None
    db_sslmode: str = "require"

    @property
    def mode(self) -> str:
        """``"url"`` (local dev), ``"aws"`` or ``"none"`` (no database)."""
        if self.database_url:
            return "url"
        if self.db_host:
            return "aws"
        return "none"

    def __repr__(self) -> str:  # never leak a password into logs/tracebacks
        url = mask_url(self.database_url) if self.database_url else None
        return (
            f"Settings(mode={self.mode!r}, database_url={url!r}, "
            f"db_host={self.db_host!r}, db_port={self.db_port}, "
            f"db_name={self.db_name!r}, db_secret_arn={self.db_secret_arn!r}, "
            f"db_sslmode={self.db_sslmode!r})"
        )

    __str__ = __repr__


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key, "").strip()
    return value or None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    database_url = _get(env, "DATABASE_URL")
    host = _get(env, "GPUSA_DB_HOST")
    name = _get(env, "GPUSA_DB_NAME")
    secret_arn = _get(env, "GPUSA_DB_SECRET_ARN")
    sslmode = _get(env, "GPUSA_DB_SSLMODE") or "require"
    port_raw = _get(env, "GPUSA_DB_PORT") or "5432"

    if sslmode not in VALID_SSLMODES:
        raise ConfigError(f"GPUSA_DB_SSLMODE must be one of {VALID_SSLMODES}, got {sslmode!r}")
    try:
        port = int(port_raw)
    except ValueError:
        raise ConfigError(f"GPUSA_DB_PORT must be a number, got {port_raw!r}") from None

    if database_url and host:
        raise ConfigError(
            "Both DATABASE_URL and GPUSA_DB_HOST are set. DATABASE_URL is for "
            "local development only; unset it on servers."
        )
    if host:
        missing = [k for k, v in (("GPUSA_DB_NAME", name), ("GPUSA_DB_SECRET_ARN", secret_arn)) if not v]
        if missing:
            raise ConfigError(f"GPUSA_DB_HOST is set but {', '.join(missing)} is missing")

    return Settings(
        database_url=database_url,
        db_host=host,
        db_port=port,
        db_name=name,
        db_secret_arn=secret_arn,
        db_sslmode=sslmode,
    )
