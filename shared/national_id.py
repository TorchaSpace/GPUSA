"""Turkish national ID numbers (T.C. Kimlik No) for employee records.

Pure: no Qt, no SQL. Used by the depot and dealership staff forms, the
repositories (local SQLite) and, mirrored, the server.

Rules of the number itself (also used for foreigners' YKN numbers, which
start with 99): 11 digits, the first is not 0, digit 10 and digit 11 are
check digits. Checking them catches almost every typo at the counter.

Lists never show the whole number: mask() keeps the first 3 and last 2
digits ("123******90"). The server only ever sends that masked form.
"""

from __future__ import annotations

from shared.i18n import UserError

LENGTH = 11


def normalize(text: str | None) -> str:
    """What was typed -> digits only (spaces, dashes and dots dropped)."""
    return "".join(ch for ch in (text or "") if not ch.isspace() and ch not in "-.")


def _error(text: str | None) -> UserError | None:
    value = normalize(text)
    if not value:
        return UserError("err.national_id_required")
    if not value.isascii() or not value.isdigit() or len(value) != LENGTH:
        return UserError("err.national_id_format", n=LENGTH)
    if value[0] == "0" or not checksum_ok(value):
        return UserError("err.national_id_invalid")
    return None


def problem(text: str | None) -> str | None:
    """Why `text` is not a valid national ID, as a message fit to show, or None."""
    error = _error(text)
    return str(error) if error else None


def checksum_ok(value: str) -> bool:
    digits = [int(ch) for ch in value]
    odd = sum(digits[0:9:2])
    even = sum(digits[1:8:2])
    tenth = (odd * 7 - even) % 10
    eleventh = sum(digits[:10]) % 10
    return digits[9] == tenth and digits[10] == eleventh


def validate(text: str | None) -> str:
    """The normalized number, or UserError (a ValueError) with the reason."""
    error = _error(text)
    if error:
        raise error
    return normalize(text)


def mask(value: str | None) -> str:
    """"123******90" for display; "—" when unknown. A value that is already masked comes back as it is."""
    value = (value or "").strip()
    if not value:
        return "—"
    if "*" in value or len(value) < 6:
        return value
    return value[:3] + "*" * (len(value) - 5) + value[-2:]
