"""A small client for the cloud API (server/), standard library only, no Qt.

Used by database/remote.py (every sign-in on a computer that has a server
address) and by the Admin "Server" page. Keeps the sign-in token in memory only
(never on disk).
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

ALLOW_HTTP_ENV = "GPUSA_ALLOW_HTTP"
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".ts.net", ".localhost")
_SHARED_RANGE = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT; what Tailscale hands out


class ServerError(Exception):
    """The server said no, or could not be reached. ``str(error)`` is fit to show."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def normalize_url(text: str) -> str:
    """'  192.168.1.5:8000/ ' -> 'http://192.168.1.5:8000'. Empty stays empty."""
    url = (text or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "http://" + url
    return url


def insecure_problem(url: str) -> str | None:
    """Why the apps must not talk to `url`, or None. Badges, PINs and sign-in tokens travel in every request,
    so plain http is only accepted inside a local network (a private or loopback address, a name without a
    dot or a local one); anywhere else the address must be https. GPUSA_ALLOW_HTTP=1 lifts this (tests of an
    internet server before it has a certificate)."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return "That is not a server address."
    if parts.scheme not in ("http", "https") or not host:
        return "A server address starts with http:// or https://."
    if parts.scheme == "https" or (os.environ.get(ALLOW_HTTP_ENV) or "").strip().lower() in ("1", "true", "yes", "on"):
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        local = "." not in host or host.lower().endswith(_LOCAL_SUFFIXES)
    else:
        local = ip.is_private or ip.is_loopback or ip.is_link_local or (ip.version == 4 and ip in _SHARED_RANGE)
    if local:
        return None
    return ("That address is on the internet and not encrypted: PINs would travel in the open. "
            "Use its https:// address.")


class ServerClient:
    def __init__(self, base_url: str, timeout: float = 6.0):
        self.base_url = normalize_url(base_url)
        self.timeout = timeout
        self.token: str | None = None

    def _request(self, method: str, path: str, body: dict | None = None):
        if not self.base_url:
            raise ServerError("Enter the server address first.")
        if problem := insecure_problem(self.base_url):
            raise ServerError(problem)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else None  # 204 (deletes, PIN changes) has no body
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
            except (ValueError, AttributeError):
                pass
            if not isinstance(detail, str) or not detail:
                detail = f"The server answered {exc.code}."
            raise ServerError(detail, exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ConnectionRefusedError):  # "[Errno 61] Connection refused" means nothing to a cashier
                reason = "nothing answered at that address - is the server running?"
            elif isinstance(reason, (TimeoutError, socket.timeout)):
                reason = "it did not answer in time - is it on, and on the same network?"
            raise ServerError(f"Cannot reach the server ({reason}).") from None

    def login(self, badge_id: str, pin: str, area: str = "admin", terminal: str = "admin-test") -> dict:
        """Sign in; keeps the token for later calls. Returns who signed in."""
        who = self._request("POST", "/auth/login",
                            {"badge_id": badge_id, "pin": pin, "area": area, "terminal": terminal})
        self.token = who["token"]
        return {**{k: who[k] for k in ("badge_id", "name", "role", "area")}, "is_root": bool(who.get("is_root"))}

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, body: dict):
        return self._request("POST", path, body)

    def patch(self, path: str, body: dict):
        return self._request("PATCH", path, body)

    def put(self, path: str, body: dict):
        return self._request("PUT", path, body)

    def delete(self, path: str):
        return self._request("DELETE", path)
