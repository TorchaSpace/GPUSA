"""Server settings, read from environment variables only.

Nothing secret is ever written in the repo: the database host and user come
from the environment, and the database password is either a short-lived IAM
token minted at connect time (the default, see server/db.py) or, for local
development only, GPUSA_DB_PASSWORD / DATABASE_URL from an untracked .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    host: str
    port: int = 5432
    name: str = "postgres"
    user: str = "postgres"
    region: str = ""
    sslmode: str = "require"
    auth: str = "iam"  # "iam" | "password"
    password: str = field(default="", repr=False)
    database_url: str = field(default="", repr=False)

    @property
    def uses_iam(self) -> bool:
        return self.auth == "iam" and not self.database_url


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env

    def get(key: str, default: str = "") -> str:
        return (e.get(key) or default).strip()

    database_url = get("DATABASE_URL")
    auth = get("GPUSA_DB_AUTH", "iam").lower()
    if auth not in {"iam", "password"}:
        raise ConfigError("GPUSA_DB_AUTH must be 'iam' or 'password'")

    host = get("GPUSA_DB_HOST")
    if not database_url:
        if not host:
            raise ConfigError("GPUSA_DB_HOST (or DATABASE_URL for local dev) is required")
        if auth == "iam" and not get("GPUSA_DB_REGION", get("AWS_REGION")):
            raise ConfigError("GPUSA_DB_REGION is required for IAM database auth")
        if auth == "password" and not get("GPUSA_DB_PASSWORD"):
            raise ConfigError("GPUSA_DB_PASSWORD is required when GPUSA_DB_AUTH=password")

    try:
        port = int(get("GPUSA_DB_PORT", "5432"))
    except ValueError as exc:
        raise ConfigError("GPUSA_DB_PORT must be a number") from exc

    return Settings(
        host=host,
        port=port,
        name=get("GPUSA_DB_NAME", "postgres"),
        user=get("GPUSA_DB_USER", "postgres"),
        region=get("GPUSA_DB_REGION", get("AWS_REGION")),
        sslmode=get("GPUSA_DB_SSLMODE", "require"),
        auth=auth,
        password=get("GPUSA_DB_PASSWORD"),
        database_url=database_url,
    )
