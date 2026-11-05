"""PIN hashing and login tokens.

The PIN hash format is the desktop apps' own
(``pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>``, see shared/auth.py),
so accounts copied from the desktop database sign in here unchanged. This
module is deliberately standalone: the server never imports desktop code.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000  # same as the desktop apps; older hashes are upgraded at sign-in
TOKEN_HOURS = 12
JWT_ALG = "HS256"


def hash_pin(pin: str, *, iterations: int | None = None, salt: bytes | None = None) -> str:
    iterations = iterations or ITERATIONS
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", (pin or "").encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def needs_rehash(stored: str) -> bool:
    try:
        algorithm, iterations, _salt, _digest = stored.split("$")
        return algorithm == ALGORITHM and int(iterations) < ITERATIONS
    except ValueError:
        return False


@functools.lru_cache(maxsize=1)
def dummy_hash() -> str:
    """Checked when the badge is unknown, so that case takes as long as a real one."""
    return hash_pin("0000-not-a-real-pin")


def normalize_badge_id(text: str | None) -> str:
    return (text or "").strip().upper()


def pin_fingerprint(pin_hash: str) -> str:
    """A short fingerprint of the stored PIN hash. It goes into the token, so changing or resetting a PIN
    signs the person out everywhere (the old tokens carry the old fingerprint)."""
    return hashlib.sha256(pin_hash.encode()).hexdigest()[:16]


def make_token(secret: str, *, account_id: int, badge_id: str, name: str, role: str, area: str,
               pin_hash: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    claims = {
        "sub": str(account_id), "badge": badge_id, "name": name, "role": role, "area": area,
        "pf": pin_fingerprint(pin_hash),
        "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=TOKEN_HOURS)).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=JWT_ALG)


def read_token(secret: str, token: str) -> dict:
    """The token's claims, or ``jwt.InvalidTokenError`` (bad signature, expired, ...)."""
    return jwt.decode(token, secret, algorithms=[JWT_ALG], options={"require": ["exp", "sub"]})
