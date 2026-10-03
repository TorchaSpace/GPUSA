"""The recovery code: a printed-on-paper key to Admin, for when the
administrator PIN is forgotten.

Shown once when it is created, stored only as a salted hash (like a PIN),
and replaced - never shown again - when a new one is made. Five wrong
guesses lock recovery for a while, so it can't be brute-forced at the
sign-in screen. Pure functions - no Qt, no database.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

# No 0/O, 1/I/L: a code copied from paper must not be ambiguous.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 16
GROUP = 4
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


def generate() -> str:
    """A new random code, e.g. 'K7QD-2MXH-9PRA-TC4W' (about 80 bits)."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + GROUP] for i in range(0, CODE_LENGTH, GROUP))


def normalize(text: str | None) -> str:
    """What was typed -> what is hashed: upper case, no dashes or spaces."""
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def looks_complete(text: str | None) -> bool:
    return len(normalize(text)) == CODE_LENGTH


def lock_expiry(failed: int, now: datetime) -> datetime | None:
    """After this wrong guess (`failed` counts it), when recovery unlocks -
    or None while guesses remain."""
    return now + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_FAILED_ATTEMPTS else None
