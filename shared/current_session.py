"""Who is signed in on THIS running app - one per process (each app is
its own process, with one person at the keyboard at a time).

The sign-in flows set it (admin_app/main.py, pos_app's till sign-in,
depot_app's Console); GUI code reads `actor()` when it writes something
worth attributing (a stock movement, a sale, a purchase decision) and
passes it to the repository explicitly - repositories never read this
module, so they stay testable without a global.
"""

from __future__ import annotations

from shared.auth import Actor, Session

_current: list[Session | None] = [None]


def get() -> Session | None:
    return _current[0]


def set(session: Session | None) -> None:  # noqa: A001 - mirrors get()
    _current[0] = session


def clear() -> None:
    _current[0] = None


def actor() -> Actor | None:
    session = _current[0]
    return session.actor if session is not None else None
