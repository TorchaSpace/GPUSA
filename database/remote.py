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

import threading
from urllib.parse import quote

from database.exceptions import DataAccessError, SessionInvalidError
from shared.i18n import english
from shared.server_client import ServerClient, ServerError

_lock = threading.Lock()
_client: ServerClient | None = None


class ServerUnavailableError(DataAccessError):
    """The server could not be reached (network, address, server down)."""

    def __init__(self, reason: str):
        super().__init__(english("err.server_unreachable", reason=reason))
        self._localize("err.server_unreachable", reason=reason)


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


def use_server(client: ServerClient | None) -> None:
    """Switch server mode on with a signed-in client, or off with None."""
    global _client
    if client is not None and not client.token:
        raise ValueError("sign the client in first")
    with _lock:
        _client = client


def active() -> bool:
    return _client is not None


def server_url() -> str | None:
    client = _client
    return client.base_url if client is not None else None


def path(*parts: str) -> str:
    """'/products/' + a barcode, safely quoted for a URL."""
    return "/" + "/".join(quote(str(p), safe="") for p in parts)


def call(method: str, url_path: str, body: dict | None = None, *, errors: dict | None = None):
    """Do one request in server mode and return the decoded JSON.

    ``errors`` maps an HTTP status to a callable building the exception to
    raise (e.g. ``{404: lambda: ProductNotFoundError(barcode)}``). Otherwise:
    401 -> SessionInvalidError (the server's own live role check said no),
    no answer -> ServerUnavailableError, anything else -> ServerRefusedError
    with the server's message."""
    client = _client
    if client is None:
        raise RuntimeError("server mode is off")
    try:
        if method == "GET":
            return client.get(url_path)
        if method == "POST":
            return client.post(url_path, body or {})
        if method == "PATCH":
            return client.patch(url_path, body or {})
        if method == "PUT":
            return client.put(url_path, body or {})
        raise ValueError(method)
    except ServerError as exc:
        if errors and exc.status in errors:
            raise errors[exc.status]() from None
        if exc.status == 401:
            raise SessionInvalidError() from None
        if exc.status is None:
            raise ServerUnavailableError(str(exc)) from None
        raise ServerRefusedError(str(exc), exc.status) from None
