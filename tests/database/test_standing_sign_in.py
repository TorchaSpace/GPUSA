"""The Depot's standing sign-in (database/remote.keep_standing) and one-off approvals.

The Floor is everyone's screen, but the server answers only someone who signed in. Whoever opens the depot
is kept as the standing sign-in: a manager signing out of the Console, or a token the server stopped
taking, goes back to that person instead of to this computer's own (empty) data."""

from __future__ import annotations

import pytest

from database import account_repository, remote
from shared import auth
from shared.server_client import ServerError
from tests.database.test_remote_mode import FakeClient

WHO = ("http://server.test", "D-001", "583920", auth.AREA_DEPOT_CONSOLE, "Depot")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import database.connection as connection

    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def sign_ins(monkeypatch):
    """Every sign-in the app makes by itself, each answered with a fresh working client."""
    made = []

    def sign_in(*who):
        client = FakeClient({("GET", "/auth/me"): {"badge_id": who[1]}})
        made.append((who, client))
        return client, {"badge_id": who[1], "name": "Deniz", "role": "depot_manager"}

    monkeypatch.setattr(remote, "sign_in", sign_in)
    return made


def test_signing_out_goes_back_to_whoever_opened_the_depot(sign_ins):
    remote.keep_standing(*WHO)
    console = FakeClient({("GET", "/auth/me"): {"badge_id": "A-1"}})
    remote.use_server(console)  # a manager signs in to the Console
    assert remote.call("GET", "/auth/me") == {"badge_id": "A-1"} and sign_ins == []

    remote.use_server(None)  # ...and out again
    assert remote.active()  # still on the server, not on this computer's own data
    assert remote.call("GET", "/auth/me") == {"badge_id": "D-001"}
    assert [who for who, _ in sign_ins] == [WHO]


def test_a_token_the_server_no_longer_takes_is_renewed_once(sign_ins):
    remote.keep_standing(*WHO)
    expired = FakeClient({("GET", "/auth/me"): ServerError("Sign in again.", 401)})
    remote.use_server(expired)
    assert remote.call("GET", "/auth/me") == {"badge_id": "D-001"}
    assert len(sign_ins) == 1 and len(expired.calls) == 1


def test_without_a_standing_sign_in_nothing_changes(sign_ins):
    from database.exceptions import SessionInvalidError

    remote.use_server(FakeClient({("GET", "/auth/me"): ServerError("Sign in again.", 401)}))
    with pytest.raises(SessionInvalidError):
        remote.call("GET", "/auth/me")
    remote.use_server(None)
    assert not remote.active() and sign_ins == []


def test_dropping_the_standing_sign_in_switches_server_mode_off(sign_ins):
    remote.keep_standing(*WHO)
    remote.use_server(None)
    assert remote.active()
    remote.drop_standing()
    assert not remote.active()


def test_an_approval_checks_someone_else_without_signing_anyone_in_or_out(monkeypatch):
    """An administrator approving a till's move to another shop used to become the till's sign-in and
    was then signed out, which dropped the till off the server."""
    seen = []
    monkeypatch.setattr(account_repository, "authenticate",
                        lambda badge, pin, area, terminal, event="sign_in": seen.append(event) or "session")
    assert account_repository.approve("A-1", "482913", auth.AREA_ADMIN, "POS 001") == "session"
    assert seen == ["pin_confirmed"]  # only a main "sign_in" switches server mode


def test_a_local_approval_is_checked_and_logged(monkeypatch):
    account_repository.create_first_admin("A-1", "Erol", "482913")
    session = account_repository.approve("a-1", "482913", auth.AREA_ADMIN, "POS 001")
    assert session.badge_id == "A-1"
    assert account_repository.list_events(1)[0]["event"] == "pin_confirmed"
    from database.exceptions import SignInFailedError

    with pytest.raises(SignInFailedError):
        account_repository.approve("A-1", "000000", auth.AREA_ADMIN, "POS 001")
