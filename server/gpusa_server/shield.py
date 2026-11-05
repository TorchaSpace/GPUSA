"""Request shield: the checks every request passes before any route code runs.

* body size: a request body over ``MAX_BODY`` bytes is refused with 413 before it is read into memory;
* guessing: an address that fails to sign in (login, recovery, first-admin set-up, a manager's PIN at a till)
  too many times in a few minutes is answered 429 for the rest of the window - per address, so one noisy PC
  cannot lock other people out, and the per-account lock still applies on top;
* NUL bytes (``%00``) in the address are refused (PostgreSQL cannot store them);
* every answer carries headers that stop browsers from guessing, framing or caching API output.

Plain ASGI on purpose: it must see the raw request, work for every route, and cost almost nothing.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque

MAX_BODY = 2 * 1024 * 1024
WINDOW_SECONDS = 300
MAX_FAILURES = 20
FAILED_STATUS = frozenset({401, 403, 423})
# (method, path prefix) whose failures count as guesses
GUESSABLE = (("POST", "/auth/login"), ("POST", "/recovery/reset"), ("POST", "/setup/first-admin"),
             ("POST", "/returns"))
HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]


class Shield:
    def __init__(self, app, *, max_failures: int = MAX_FAILURES, window: int = WINDOW_SECONDS,
                 max_body: int = MAX_BODY) -> None:
        self.app = app
        self.max_failures, self.window, self.max_body = max_failures, window, max_body
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    # --- helpers -------------------------------------------------------------

    def _recent(self, who: str, now: float) -> deque[float]:
        hits = self._failures[who]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            self._failures.pop(who, None)
            return deque()
        return hits

    @staticmethod
    async def _reply(send, status: int, detail: str, extra: list | None = None) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *HEADERS, *(extra or [])]})
        await send({"type": "http.response.body", "body": body})

    # --- ASGI ----------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        raw = scope.get("raw_path") or b""
        if b"%00" in raw or "\x00" in path or b"\x00" in scope.get("query_string", b"") \
                or b"%00" in scope.get("query_string", b""):
            return await self._reply(send, 422, "That address contains characters that are not allowed.")

        headers = dict(scope["headers"])
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_body:
            return await self._reply(send, 413, "That request is too large.")

        guess = any(method == m and path.startswith(p) for m, p in GUESSABLE)
        who = (scope.get("client") or ("?", 0))[0]
        now = time.monotonic()
        if guess:
            hits = self._recent(who, now)
            if len(hits) >= self.max_failures:
                wait = max(1, int(self.window - (now - hits[0])))
                return await self._reply(send, 429, "Too many wrong attempts from this address. Wait a few minutes.",
                                         [(b"retry-after", str(wait).encode())])

        seen = 0
        started = False

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_body:
                    raise _TooLarge
            return message

        async def shielded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = {**message, "headers": [(k, v) for k, v in message.get("headers", [])
                                                  if k.lower() not in {h for h, _ in HEADERS}] + HEADERS}
                if guess and message["status"] in FAILED_STATUS:
                    hits = self._failures[who]
                    hits.append(time.monotonic())
            await send(message)

        try:
            await self.app(scope, limited_receive, shielded_send)
        except _TooLarge:
            if not started:
                await self._reply(send, 413, "That request is too large.")


class _TooLarge(Exception):
    pass
