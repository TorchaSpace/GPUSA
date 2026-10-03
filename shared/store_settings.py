"""Store-wide settings every app shares: who the store is (receipt and
report letterhead), which alerts are on, and where the data lives.

Pure - no Qt, no database. database/settings_repository.py stores the raw
key/value text; this module says what the keys are, what the defaults are,
and how text turns into a value and back (so it can be tested alone).
"""

from __future__ import annotations

from shared.i18n import UserError
from dataclasses import dataclass, field

DEFAULT_STORE_NAME = "GPUSA"
MAX_NAME_LENGTH = 60
MAX_ADDRESS_LINES = 4
MAX_ADDRESS_LINE_LENGTH = 40  # receipts are RECEIPT_WIDTH_CHARS wide; longer lines would wrap badly

KEY_STORE_NAME = "store.name"
KEY_STORE_ADDRESS = "store.address"  # lines joined with "\n"
KEY_LOW_STOCK_ALERTS = "alerts.low_stock"
KEY_PENDING_BADGE = "alerts.pending_approvals"
KEY_LANGUAGE = "ui.language"
DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class StoreProfile:
    name: str = DEFAULT_STORE_NAME
    address_lines: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class NotificationPrefs:
    low_stock_alerts: bool = True  # depot's amber banner
    pending_approvals: bool = True  # Admin's live badge on Purchase requests / header buttons


def parse_address(text: str | None) -> tuple[str, ...]:
    """Blank lines and surrounding spaces dropped; at most MAX_ADDRESS_LINES kept."""
    lines = [line.strip() for line in (text or "").splitlines()]
    return tuple(line for line in lines if line)[:MAX_ADDRESS_LINES]


def validate_profile(name: str, address_text: str) -> StoreProfile:
    """Raise ValueError (with a message fit to show) if the input can't be saved."""
    name = " ".join((name or "").split())
    if not name:
        raise UserError("err.store_name_required")
    if len(name) > MAX_NAME_LENGTH:
        raise UserError("err.store_name_long", n=MAX_NAME_LENGTH)
    raw = [line.strip() for line in (address_text or "").splitlines() if line.strip()]
    if len(raw) > MAX_ADDRESS_LINES:
        raise UserError("err.address_lines", n=MAX_ADDRESS_LINES)
    for line in raw:
        if len(line) > MAX_ADDRESS_LINE_LENGTH:
            raise UserError("err.address_line_long", n=MAX_ADDRESS_LINE_LENGTH)
    return StoreProfile(name=name, address_lines=tuple(raw))


def profile_from(values: dict[str, str]) -> StoreProfile:
    name = " ".join((values.get(KEY_STORE_NAME) or "").split())
    return StoreProfile(name=name or DEFAULT_STORE_NAME, address_lines=parse_address(values.get(KEY_STORE_ADDRESS)))


def profile_to_values(profile: StoreProfile) -> dict[str, str]:
    return {KEY_STORE_NAME: profile.name, KEY_STORE_ADDRESS: "\n".join(profile.address_lines)}


def _flag(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def prefs_from(values: dict[str, str]) -> NotificationPrefs:
    defaults = NotificationPrefs()
    return NotificationPrefs(
        low_stock_alerts=_flag(values.get(KEY_LOW_STOCK_ALERTS), defaults.low_stock_alerts),
        pending_approvals=_flag(values.get(KEY_PENDING_BADGE), defaults.pending_approvals),
    )


def prefs_to_values(prefs: NotificationPrefs) -> dict[str, str]:
    return {
        KEY_LOW_STOCK_ALERTS: "1" if prefs.low_stock_alerts else "0",
        KEY_PENDING_BADGE: "1" if prefs.pending_approvals else "0",
    }


def language_from(values: dict[str, str], known: list[str] | tuple[str, ...] = ("en", "tr")) -> str:
    """The saved interface language, or English when unset or unknown."""
    code = (values.get(KEY_LANGUAGE) or "").strip().lower()
    return code if code in known else DEFAULT_LANGUAGE
