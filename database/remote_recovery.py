"""Server versions of the recovery code and security question.

Looking at the sign-in screen and resetting a forgotten PIN happen before anyone is signed in, so those use a
plain (token-less) client on the server address; making a code or a question needs the signed-in
administrator and goes through the normal server call. The wrong-guess counter and the lock are kept on the
server, shared by both methods."""

from __future__ import annotations

import re

from database import remote
from database.exceptions import AccountLockedError, SignInFailedError, localized_auth
from database.remote_stock import db_timestamp
from shared import paths
from shared.formatting import local_datetime_text
from shared.server_client import ServerClient, ServerError


def _public(method: str, path: str, body: dict | None = None):
    url = paths.get_server_url()
    if not url:
        raise remote.ServerUnavailableError("No server address is set.")
    client = ServerClient(url)
    try:
        return client._request(method, path, body)
    except ServerError as exc:
        if exc.status is None:
            raise remote.ServerUnavailableError(str(exc)) from None
        raise remote.ServerRefusedError(str(exc), exc.status) from None


def status() -> dict:
    return _public("GET", "/recovery/status")


def has_code() -> bool:
    return bool(status()["has_code"])


def question() -> str | None:
    return status()["question"] or None


def make_code() -> str:
    return remote.call("POST", "/recovery/code", {})["code"]


def set_question(current_pin: str, question_text: str, answer: str) -> None:
    try:
        remote.call("PUT", "/recovery/question", {"current_pin": current_pin, "question": question_text,
                                                  "answer": answer})
    except remote.ServerRefusedError as exc:
        if exc.status == 401 and "current PIN is wrong" in str(exc):
            raise SignInFailedError() from None
        if exc.status == 423:
            match = re.search(r"until (\S+?)\.?$", str(exc))
            raise AccountLockedError(match.group(1) if match else "later") from None
        raise


def reset(method: str, secret: str, badge_id: str, new_pin: str, terminal: str, new_badge: str | None = None) -> str:
    """method: 'code' or 'answer'. Returns the badge to sign in with (changed if `new_badge` was given)."""
    none_key, wrong_key = ("err.rc_none", "err.rc_wrong") if method == "code" else ("err.sq_none", "err.sq_wrong")
    try:
        answer = _public("POST", "/recovery/reset", {"method": method, "secret": secret, "badge_id": badge_id,
                                                     "new_pin": new_pin, "terminal": terminal,
                                                     "new_badge_id": new_badge or None})
        return (answer or {}).get("badge_id") or badge_id
    except remote.ServerRefusedError as exc:
        text = str(exc)
        if exc.status == 404:
            raise localized_auth(none_key) from None
        if exc.status == 423:
            match = re.search(r"until (\S+?)\.?$", text)
            when = match.group(1) if match else ""
            raise localized_auth("err.recovery_locked", when=local_datetime_text(db_timestamp(when))) from None
        if exc.status == 401:
            raise localized_auth(wrong_key) from None
        if exc.status == 403:
            raise localized_auth("err.not_active_admin") from None
        if exc.status in (409, 422):
            raise ValueError(text) from None
        raise
