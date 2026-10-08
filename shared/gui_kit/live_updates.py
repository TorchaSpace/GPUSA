"""Instant refresh when another app (or another till) writes to the database.

SQLite cannot push a change to another process, but it does keep a counter -
`PRAGMA data_version` - that moves whenever ANOTHER connection commits. Asking
for it is nearly free (no table is read), so DataWatcher asks once a second
and emits `changed` when it moved. Each window connects `changed` to "reload
what is on screen", which is how a sale at a till shows up on the Admin
dashboard within about a second without anyone pressing Refresh.

A burst of writes (a busy afternoon) is coalesced: after the first change the
signal waits `settle_ms` for the dust to settle, then no more than one signal
per `min_gap_ms` goes out, so a page is never reloaded faster than it can
paint. A locked database or a failed query just skips that tick.

When the system moves to a server, this class is the one place to swap for a
push channel (the windows only know `changed`).
"""

from __future__ import annotations

import sqlite3
import time

from PySide6.QtCore import QObject, QTimer, Signal

from database.connection import get_connection

LIVE_POLL_INTERVAL_MS = 1000
LIVE_SETTLE_MS = 250
LIVE_MIN_GAP_MS = 2000


class DataWatcher(QObject):
    changed = Signal()

    def __init__(self, interval_ms: int = LIVE_POLL_INTERVAL_MS, settle_ms: int = LIVE_SETTLE_MS,
                 min_gap_ms: int = LIVE_MIN_GAP_MS, parent: QObject | None = None):
        super().__init__(parent)
        self._conn: sqlite3.Connection | None = None
        self._version: int | None = None
        self._settle_ms = settle_ms
        self._min_gap = min_gap_ms / 1000.0
        self._last_emit = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(interval_ms)
        self._poll.timeout.connect(self.check)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._emit)

    def start(self) -> None:
        self._read_version()  # the baseline: changes before now are not news
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._settle.stop()
        self._close()

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn = None

    def _read_version(self) -> int | None:
        try:
            if self._conn is None:
                self._conn = get_connection()
            self._version = int(self._conn.execute("PRAGMA data_version").fetchone()[0])
        except Exception:  # locked / file moved: reconnect on the next tick
            self._close()
            return None
        return self._version

    def check(self) -> bool:
        """One poll. True when the database changed since the last look."""
        previous = self._version
        current = self._read_version()
        if current is None or previous is None or current == previous:
            return False
        if not self._settle.isActive():
            wait = max(self._settle_ms, int((self._last_emit + self._min_gap - time.monotonic()) * 1000))
            self._settle.start(wait)
        return True

    def _emit(self) -> None:
        self._last_emit = time.monotonic()
        self.changed.emit()
