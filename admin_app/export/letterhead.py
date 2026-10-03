"""The store name every export prints at the top: whatever Admin >
Settings > General says, or the built-in default if it can't be read."""

from __future__ import annotations

from database import settings_repository


def store_name() -> str:
    return settings_repository.safe_store_profile().name
