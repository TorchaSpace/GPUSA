"""Database credentials from AWS Secrets Manager.

Only called when ``GPUSA_DB_SECRET_ARN`` is set. ``boto3`` is imported lazily
so local development and the test suite never need AWS credentials or even
boto3 itself. On EC2 the instance role supplies the AWS credentials - no
access keys anywhere.

The secret is expected in the RDS-managed format::

    {"username": "...", "password": "...", ...}

Results are cached for a few minutes so we don't call Secrets Manager on every
new connection, but a rotated password is still picked up soon after.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

CACHE_TTL_SECONDS = 300


class SecretError(RuntimeError):
    """The secret could not be read or is not in the expected format."""


@dataclass(frozen=True)
class DbCredentials:
    username: str
    password: str

    def __repr__(self) -> str:
        return f"DbCredentials(username={self.username!r}, password='***')"


def _default_client(secret_arn: str) -> Any:
    import boto3  # lazy: only needed on AWS

    # "arn:aws:secretsmanager:<region>:<account>:secret:<name>"
    parts = secret_arn.split(":")
    region = parts[3] if len(parts) > 3 and parts[3] else None
    return boto3.client("secretsmanager", region_name=region)


_cache: dict[str, tuple[float, DbCredentials]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def fetch_db_credentials(
    secret_arn: str,
    *,
    client: Any = None,
    now: Callable[[], float] = time.monotonic,
    ttl: float = CACHE_TTL_SECONDS,
) -> DbCredentials:
    if not secret_arn:
        raise SecretError("No secret ARN given")

    with _lock:
        hit = _cache.get(secret_arn)
        if hit and now() - hit[0] < ttl:
            return hit[1]

    sm = client if client is not None else _default_client(secret_arn)
    try:
        response = sm.get_secret_value(SecretId=secret_arn)
    except Exception as exc:  # botocore errors; don't leak the payload
        raise SecretError(f"Could not read secret {secret_arn}: {type(exc).__name__}") from exc

    raw = response.get("SecretString")
    if not raw:
        raise SecretError(f"Secret {secret_arn} has no SecretString")
    try:
        data = json.loads(raw)
        creds = DbCredentials(username=data["username"], password=data["password"])
    except (ValueError, KeyError, TypeError):
        raise SecretError(f"Secret {secret_arn} is not JSON with username/password") from None

    with _lock:
        _cache[secret_arn] = (now(), creds)
    return creds
