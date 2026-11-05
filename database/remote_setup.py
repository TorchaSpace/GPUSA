"""Server version of 'create the first administrator'.

Nobody is signed in yet, so this uses a plain (token-less) client on the server address, like the recovery
screens. The server opens this door only while it has no active administrator."""

from __future__ import annotations

from database import remote, remote_recovery
from database.exceptions import localized_auth


def needs_first_admin() -> bool:
    """True when the server has no administrator yet, so Admin should offer the set-up screen. If the server
    cannot be reached this says False: the sign-in screen is where 'server unreachable' is explained."""
    try:
        return bool(remote_recovery._public("GET", "/setup/status")["needs_first_admin"])
    except remote.ServerUnavailableError:
        return False


def create_first_admin(badge_id: str, name: str, pin: str, terminal: str) -> None:
    try:
        remote_recovery._public("POST", "/setup/first-admin", {"badge_id": badge_id, "name": name, "pin": pin,
                                                               "terminal": terminal})
    except remote.ServerRefusedError as exc:
        if exc.status == 409:
            raise localized_auth("err.admin_exists") from None
        if exc.status == 422:
            raise ValueError(str(exc).removeprefix("The server said no: ")) from None
        raise
