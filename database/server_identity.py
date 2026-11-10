"""Is this really our server? Asked before a PIN is sent to an address, or a new address is believed.

The apps find the server by themselves on the network, and whatever answers "I am the GPUSA server" would
get the next badge and PIN. So the first sign-in that reaches a server pairs this computer with it: the
server hands out a key only it can compute (server/gpusa_server/identity_routes.py), kept in config.json.
From then on a server must prove it knows that key:

* before every sign-in: an address that answers but cannot prove itself gets no PIN (ServerNotOursError);
* when the saved address stopped answering and another one is found on the network: it is adopted only
  after it proved itself (database/server_discovery.py).

Typing a server address on purpose (the sign-in screens, Admin > Server) forgets the pairing, so the next
sign-in pairs with that server. This guards against an impostor on the network, not against someone sitting
between this computer and the real server - that needs an https address.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from database.exceptions import AuthError
from shared import paths
from shared.i18n import english
from shared.server_client import ServerClient, ServerError


class ServerNotOursError(AuthError):
    """Something answers at the server address, but it is not the server this computer was connected to."""

    def __init__(self):
        super().__init__(english("err.server_not_ours"))
        self._localize("err.server_not_ours")


def proves_itself(url: str, timeout: float = 4.0) -> bool | None:
    """True: the server at `url` knows this computer's pairing key. False: it answered without proving it.
    None: nothing to check against (this computer is not paired yet) or nothing answered."""
    pairing = paths.get_server_pairing()
    if pairing is None:
        return None
    nonce = secrets.token_hex(16)
    try:
        answer = ServerClient(url, timeout=timeout).get(f"/identity?device={pairing['device']}&nonce={nonce}")
        proof = str((answer or {}).get("proof", ""))
        expected = hmac.new(bytes.fromhex(pairing["key"]), bytes.fromhex(nonce), hashlib.sha256).hexdigest()
    except ServerError as exc:
        return None if exc.status is None else False
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(proof, expected)


def require_ours(url: str) -> None:
    """Raise ServerNotOursError when the server at `url` answers but is not the one this computer knows."""
    if proves_itself(url) is False:
        raise ServerNotOursError()


def pair(client: ServerClient) -> bool:
    """After a sign-in: remember this server, unless this computer is paired already. Best effort - an older
    server without pairing simply leaves the computer unpaired."""
    if paths.get_server_pairing() is not None:
        return False
    device = secrets.token_hex(16)
    try:
        key = str((client.get(f"/auth/pairing?device={device}") or {}).get("key", ""))
        bytes.fromhex(key)
    except (ServerError, ValueError, AttributeError):
        return False
    if len(key) != 64:
        return False
    try:
        paths.set_server_pairing({"device": device, "key": key})
    except OSError:
        return False
    return True
