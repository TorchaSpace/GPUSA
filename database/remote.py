"""Server mode: send some repository calls to the cloud API instead of SQLite.

Off by default, so every app works exactly as before on its local
``shared_backend.db``. Admin > Server (test) switches it on after signing in
to the server (``use_server(client)``); from then on, in that Admin process
only, the functions that have a server version (see database/remote_products.py)
read and write the server, and the rest stay local. Switching it off
(``use_server(None)``) or restarting the app goes back to local.

This module is the only place in database/ that talks to the network. Server
answers come back as the same DataAccessError subclasses the GUI already
catches (DATABASE_ERRORS), so the screens need no change.
"""

from __future__ import annotations

import json
import re
import threading
import time
from urllib.parse import quote

from database.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    DataAccessError,
    NotAllowedError,
    SessionInvalidError,
    SignInFailedError,
)
from shared.i18n import english
from shared.server_client import ServerClient, ServerError

_lock = threading.Lock()
_client: ServerClient | None = None
# Signed in at the till while the server could not be reached: who, to sign in
# to the server for real as soon as it answers (kept in memory only, for this
# sign-in, so the queued sales can be sent without asking again).
_pending: tuple[str, str, str, str, str] | None = None

# After a request finds the server unreachable, the next few seconds' calls fail at once instead of each
# waiting out the network timeout again - a screen that asks the server five things would otherwise freeze
# for half a minute on every click while the server is down. The first call after the pause tries for real.
_RETRY_AFTER_SECONDS = 8.0
_down_until = 0.0

# Reads a till needs while offline: their last answer is kept in the local
# server_cache table and served when the server cannot be reached.
CACHED_PREFIXES = ("/products", "/stock/products-at", "/stock/reorder-levels", "/dealerships", "/warehouses",
                   "/shipments", "/stock-requests")


class ServerUnavailableError(DataAccessError):
    """The server could not be reached (network, address, server down)."""

    def __init__(self, reason: str):
        super().__init__(english("err.server_unreachable", reason=reason))
        self._localize("err.server_unreachable", reason=reason)


class _ServerPaused(ServerUnavailableError):
    """Raised without touching the network while the server is in its short 'not answering' pause."""


class ServerRefusedError(DataAccessError):
    """The server answered with an error; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int | None):
        super().__init__(english("err.server_refused", message=message))
        self._localize("err.server_refused", message=message)
        self.status = status


class NotOnServerYetError(DataAccessError):
    """Server mode is on but this action has no server version yet."""

    def __init__(self):
        super().__init__(english("err.server_not_yet"))
        self._localize("err.server_not_yet")


def sign_in(url: str, badge_id: str, pin: str, area: str, terminal: str) -> tuple[ServerClient, dict]:
    """Sign in to the server at `url`; returns (signed-in client, who). The
    server's refusals come back as the desktop's own sign-in errors; no answer
    at all is ServerUnavailableError."""
    from shared import auth  # local: shared.auth is light, but keep this module's imports minimal

    client = ServerClient(url)
    try:
        who = client.login(badge_id, pin, area=area, terminal=terminal or "")
    except ServerError as exc:
        text = str(exc)
        if exc.status is None:
            raise ServerUnavailableError(text) from None
        if exc.status == 401:
            raise SignInFailedError() from None
        if exc.status == 423:
            match = re.search(r"until (\S+?)\.?$", text)
            raise AccountLockedError(match.group(1) if match else "later") from None
        if exc.status == 403 and "switched off" in text:
            raise AccountDisabledError() from None
        if exc.status == 403:
            match = re.match(r"A (\w+) account", text)
            role = auth.role_label(match.group(1)) if match else "this"
            raise NotAllowedError(role, auth.AREA_LABELS.get(area, area)) from None
        raise ServerRefusedError(text, exc.status) from None
    return client, who


def use_server(client: ServerClient | None) -> None:
    """Switch server mode on with a signed-in client, or off with None."""
    global _client, _pending
    if client is not None and not client.token:
        raise ValueError("sign the client in first")
    global _down_until
    with _lock:
        _client = client
        _pending = None
        _down_until = 0.0


def use_server_offline(url: str, badge_id: str, pin: str, area: str, terminal: str) -> None:
    """Server mode while the server cannot be reached: reads come from the local
    cache, sales queue up, and the first call that reaches the server signs in."""
    global _client, _pending, _down_until
    with _lock:
        _client = None
        _pending = (url, badge_id, pin, area, terminal)
        _down_until = 0.0


def retry_now() -> None:
    """End the short 'not answering' pause: the next call really tries the network. For background
    syncing, which runs off the GUI thread and may wait out a timeout."""
    global _down_until
    _down_until = 0.0


def active() -> bool:
    return _client is not None or _pending is not None


def online() -> bool:
    """Signed in to the server (as opposed to working from the cache)."""
    return _client is not None


def server_url() -> str | None:
    client = _client
    if client is not None:
        return client.base_url
    pending = _pending
    return pending[0] if pending is not None else None


def _current_client() -> ServerClient:
    """The signed-in client; while offline, try to sign in now (ServerUnavailableError if still offline)."""
    global _client, _pending
    client = _client
    if client is not None:
        return client
    pending = _pending
    if pending is None:
        raise RuntimeError("server mode is off")
    signed_in, _who = sign_in(*pending)
    with _lock:
        if _pending == pending:
            _client, _pending = signed_in, None
    return signed_in


def path(*parts: str) -> str:
    """'/products/' + a barcode, safely quoted for a URL."""
    return "/" + "/".join(quote(str(p), safe="") for p in parts)


def call(method: str, url_path: str, body: dict | None = None, *, errors: dict | None = None):
    """Do one request in server mode and return the decoded JSON.

    ``errors`` maps an HTTP status to a callable building the exception to
    raise (e.g. ``{404: lambda: ProductNotFoundError(barcode)}``). Otherwise:
    401 -> SessionInvalidError (the server's own live role check said no),
    no answer -> ServerUnavailableError, anything else -> ServerRefusedError
    with the server's message. A GET under CACHED_PREFIXES that gets no
    answer returns its last cached answer instead, when there is one."""
    global _down_until
    cached = method == "GET" and url_path.startswith(CACHED_PREFIXES)
    try:
        if time.monotonic() < _down_until:
            raise _ServerPaused("It did not answer a moment ago; trying again in a few seconds.")
        client = _current_client()
        if method == "GET":
            result = client.get(url_path)
        elif method == "POST":
            result = client.post(url_path, body or {})
        elif method == "PATCH":
            result = client.patch(url_path, body or {})
        elif method == "PUT":
            result = client.put(url_path, body or {})
        else:
            raise ValueError(method)
    except ServerUnavailableError as exc:
        if not isinstance(exc, _ServerPaused):
            _down_until = time.monotonic() + _RETRY_AFTER_SECONDS
        if cached and (hit := cache_get(url_path)) is not None:
            return hit
        raise
    except ServerError as exc:
        if errors and exc.status in errors:
            raise errors[exc.status]() from None
        if exc.status == 401:
            raise SessionInvalidError() from None
        if exc.status is None:
            _down_until = time.monotonic() + _RETRY_AFTER_SECONDS
            if cached and (hit := cache_get(url_path)) is not None:
                return hit
            raise ServerUnavailableError(str(exc)) from None
        raise ServerRefusedError(str(exc), exc.status) from None
    _down_until = 0.0
    if cached:
        cache_put(url_path, result)
    return result


# --- the local cache (database/migrations.py v11) -------------------------------

def _cache_key(url_path: str) -> str:
    return f"{server_url() or ''}|{url_path}"


def cache_put(url_path: str, value) -> None:
    from database.connection import connection_scope  # local: connection imports nothing of ours

    with connection_scope() as conn:
        conn.execute("INSERT INTO server_cache (key, body) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET "
                     "body = excluded.body, fetched_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                     (_cache_key(url_path), json.dumps(value)))


def cache_get(url_path: str):
    from database.connection import connection_scope

    with connection_scope() as conn:
        row = conn.execute("SELECT body FROM server_cache WHERE key = ?", (_cache_key(url_path),)).fetchone()
    return json.loads(row[0]) if row is not None else None


def cache_update(url_path: str, change) -> None:
    """Apply `change(value) -> value` to a cached answer (a till takes sold units
    off its cached shelf while offline). Nothing happens when none is cached."""
    value = cache_get(url_path)
    if value is not None:
        cache_put(url_path, change(value))
