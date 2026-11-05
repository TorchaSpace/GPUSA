"""A fresh PC finds the server by itself.

Only a PC with no server address AND no administrator in its own database file (a new download, nothing to
lose) looks for one; an installation that already has local data keeps using it until someone connects it on
purpose in Admin > Server (test). The address found is saved like a typed one (config.json), so the next start
does not search again. Opt out with GPUSA_DISCOVERY=0."""

from __future__ import annotations

import os
from collections.abc import Callable

from shared import discovery, paths


def adopt_server_if_fresh(local_has_administrator: Callable[[], bool]) -> str | None:
    """Look for a server and remember it. Returns the address saved, or None (nothing changed).
    `local_has_administrator` is only called when no server address is set yet."""
    if paths.get_server_url():
        return None
    if os.environ.get("GPUSA_DISCOVERY", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if local_has_administrator():
        return None
    try:
        url = discovery.find_server()
    except OSError:
        return None
    if url:
        paths.set_server_url(url)
    return url
