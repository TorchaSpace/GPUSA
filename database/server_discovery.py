"""The apps find the server by themselves when they start.

- No server address saved yet: look for the one server on the network and remember it (config.json), so every
  app on this PC signs in to it and reads its database from then on. Nobody has to open a "server test" page.
- An address is saved but nothing answers there any more (the server PC got a new IP): look again and, if the
  server answers somewhere else AND proves it is the server this computer signed in to before
  (database/server_identity.py), remember the new address. A computer that never signed in keeps its
  address: there is nothing to tell the real server from an impostor by.

An address given with the GPUSA_SERVER_URL environment variable is never second-guessed. Opt out of all of
this with GPUSA_DISCOVERY=0 (a PC that must stay on its own local database file)."""

from __future__ import annotations

import os

from database import server_identity
from shared import discovery, paths


def adopt_server() -> str | None:
    """Look for the server and remember it. Returns the address newly saved, or None (nothing changed)."""
    if os.environ.get("GPUSA_DISCOVERY", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if (os.environ.get(paths.SERVER_URL_ENV) or "").strip():
        return None
    saved = paths.get_server_url()
    try:
        if saved and discovery.is_gpusa(saved):
            return None
        url = discovery.find_server()
    except OSError:
        return None
    if not url or url == saved:
        return None
    if saved and server_identity.proves_itself(url) is not True:
        return None
    discovery.save_server(url, typed=False)
    return url
