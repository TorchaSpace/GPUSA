"""A small client for the cloud API (server/), standard library only, no Qt.

Used by the Admin "Server (test)" page, and by database/remote.py when that
page switches "server mode" on. Keeps the sign-in token in memory only
(never on disk).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


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


class ServerClient:
    def __init__(self, base_url: str, timeout: float = 6.0):
        self.base_url = normalize_url(base_url)
        self.timeout = timeout
        self.token: str | None = None

    def _request(self, method: str, path: str, body: dict | None = None):
        if not self.base_url:
            raise ServerError("Enter the server address first.")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
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
            raise ServerError(f"Cannot reach the server ({reason}).") from None

    def login(self, badge_id: str, pin: str, area: str = "admin", terminal: str = "admin-test") -> dict:
        """Sign in; keeps the token for later calls. Returns who signed in."""
        who = self._request("POST", "/auth/login",
                            {"badge_id": badge_id, "pin": pin, "area": area, "terminal": terminal})
        self.token = who["token"]
        return {k: who[k] for k in ("badge_id", "name", "role", "area")}

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, body: dict):
        return self._request("POST", path, body)

    def patch(self, path: str, body: dict):
        return self._request("PATCH", path, body)

    def put(self, path: str, body: dict):
        return self._request("PUT", path, body)
