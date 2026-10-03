"""All SQL for `app_settings` (store-wide key/value settings), plus
the typed loaders built on shared/store_settings.py.

Readers never raise on a missing table value - an unset key is simply the
default - and the load_* helpers swallow DataAccessError-style failures
only where the caller asks (see `safe_*`), so a receipt can still print if
the settings can't be read.
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import DataAccessError
from shared import i18n
from shared import store_settings as ss


def get_all() -> dict[str, str]:
    with connection_scope() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_many(values: dict[str, str]) -> None:
    """Upsert every pair in one transaction."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                    (key, value),
                )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def load_store_profile() -> ss.StoreProfile:
    return ss.profile_from(get_all())


def save_store_profile(profile: ss.StoreProfile) -> None:
    set_many(ss.profile_to_values(profile))


def save_profile_and_language(profile: ss.StoreProfile, language: str) -> None:
    """Store profile and interface language together, in ONE transaction -
    either both are saved or neither. Raises ValueError for an unknown
    language code."""
    code = (language or "").strip().lower()
    known = [known_code for known_code, _name in i18n.available_languages()]
    if code not in known:
        raise ValueError(f"Unknown language {language!r}")
    values = ss.profile_to_values(profile)
    values[ss.KEY_LANGUAGE] = code
    set_many(values)


def load_notifications() -> ss.NotificationPrefs:
    return ss.prefs_from(get_all())


def save_notifications(prefs: ss.NotificationPrefs) -> None:
    set_many(ss.prefs_to_values(prefs))


def safe_store_profile() -> ss.StoreProfile:
    """The profile, or the default when the database can't be read - for
    code paths (receipt printing) that must not fail over a letterhead."""
    try:
        return load_store_profile()
    except (DataAccessError, sqlite3.Error):
        return ss.StoreProfile()


def safe_notifications() -> ss.NotificationPrefs:
    try:
        return load_notifications()
    except (DataAccessError, sqlite3.Error):
        return ss.NotificationPrefs()


def safe_language() -> str:
    """The saved interface language code; English if unset or unreadable."""
    try:
        return ss.language_from(get_all())
    except (DataAccessError, sqlite3.Error):
        return ss.DEFAULT_LANGUAGE


def save_language(code: str) -> None:
    set_many({ss.KEY_LANGUAGE: code})
