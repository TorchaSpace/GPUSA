"""Sign-in rules shared by all three apps (no Qt, no SQL): roles and what
each may open, PIN rules and hashing, and the Session / Actor objects the
apps pass around once someone has signed in.

Accounts are employees (Workforce) who've been given a role and a PIN -
see database/account_repository.py for the storage, lockout and audit
trail. Where signing in is required (Erol's "per role" decision):
- admin_app: an administrator, at startup.
- pos_app: a cashier (or an administrator) at the till; "Switch cashier"
  hands over at shift change.
- depot_app: the Floor stays an open kiosk; the Manager Console needs a
  depot manager (or an administrator), and the Manager Portal inside it
  asks that person's PIN again and locks itself after 10 minutes.

PINs are digits only, never stored: each is salted and run through
PBKDF2-HMAC-SHA256 (hashlib, standard library - no new dependency), and
compared in constant time. A PIN is short, so the real protection is the
lockout: after MAX_FAILED_ATTEMPTS wrong PINs in a row the account is
locked for LOCK_MINUTES (an administrator can unlock it sooner).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from shared.i18n import LazyLabels, tr

ROLES = ("admin", "depot_manager", "cashier")
ROLE_LABELS = LazyLabels("auth.role", ROLES)

# Which roles may open which part of the system.
AREA_ADMIN = "admin"
AREA_DEPOT_CONSOLE = "depot_console"
AREA_POS = "pos"
AREA_LABELS = LazyLabels("auth.area", (AREA_ADMIN, AREA_DEPOT_CONSOLE, AREA_POS))  # as in "can't open ..."
AREA_NAMES = LazyLabels("auth.area_name", (AREA_ADMIN, AREA_DEPOT_CONSOLE, AREA_POS))  # as a column value
ALLOWED_ROLES = {
    AREA_ADMIN: frozenset({"admin"}),
    AREA_DEPOT_CONSOLE: frozenset({"admin", "depot_manager"}),
    AREA_POS: frozenset({"admin", "cashier"}),
}

MIN_PIN_LENGTH = 4
MIN_ADMIN_PIN_LENGTH = 6
MAX_PIN_LENGTH = 12
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 5
PORTAL_AUTO_LOCK_SECONDS = 10 * 60  # the Manager Portal mockup's 10-minute session
IDLE_LOCK_SECONDS = 15 * 60  # Admin and POS sign out after this long with no input (same as the depot Console)

_ALGORITHM = "pbkdf2_sha256"
DEFAULT_PBKDF2_ITERATIONS = 200_000
PBKDF2_ITERATIONS = DEFAULT_PBKDF2_ITERATIONS  # tests lower this; the apps never do


def normalize_badge_id(text: str | None) -> str:
    """What was typed or scanned -> the badge ID as stored: surrounding
    spaces dropped, upper case. ("b-7 " and "B-7" are the same badge.)
    Lookups of rows saved before this rule also match them
    case-insensitively - see employee_repository.find_row()."""
    return (text or "").strip().upper()


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def can_open(role: str, area: str) -> bool:
    return role in ALLOWED_ROLES.get(area, frozenset())


def pin_problem(pin: str, role: str) -> str | None:
    """Why `pin` isn't acceptable for an account with `role`, or None."""
    pin = pin or ""
    minimum = MIN_ADMIN_PIN_LENGTH if role == "admin" else MIN_PIN_LENGTH
    if not pin.isdigit() or not pin.isascii():
        return tr("auth.pin_digits")
    if len(pin) < minimum:
        return tr("auth.pin_short").format(role=role_label(role).lower(), n=minimum)
    if len(pin) > MAX_PIN_LENGTH:
        return tr("auth.pin_long").format(n=MAX_PIN_LENGTH)
    if len(set(pin)) == 1 or pin in "0123456789012" or pin in "9876543210987":
        return tr("auth.pin_sequence")
    return None


def hash_pin(pin: str, *, iterations: int | None = None, salt: bytes | None = None) -> str:
    """'pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>' - self-describing,
    so the iteration count can be raised later without breaking old PINs."""
    iterations = iterations or PBKDF2_ITERATIONS
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
    return f"{_ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", (pin or "").encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


@dataclass(frozen=True)
class Actor:
    """Who did something - stored as a text snapshot ("Murat Yılmaz · B-100")
    on the rows it touched, so renaming or deleting the employee later
    never rewrites history."""

    badge_id: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.name} · {self.badge_id}"


def actor_label(actor: Actor | None) -> str | None:
    return actor.label if actor is not None else None


@dataclass(frozen=True)
class Session:
    """Someone signed in on this terminal."""

    account_id: int
    badge_id: str
    name: str
    role: str
    signed_in_at: str  # db timestamp
    area: str
    terminal: str

    @property
    def actor(self) -> Actor:
        return Actor(self.badge_id, self.name)

    @property
    def first_name(self) -> str:
        return (self.name.split() or ["there"])[0]

    @property
    def role_label(self) -> str:
        return role_label(self.role)

    @property
    def initials(self) -> str:
        parts = self.name.split()
        return "".join(part[0] for part in parts[:2]).upper() or "?"

    def can_open(self, area: str) -> bool:
        return can_open(self.role, area)
