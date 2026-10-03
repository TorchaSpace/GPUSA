"""Generic "re-run a query on an interval and hand the result to a callback" mechanic.

Why this exists: SQLite (unlike e.g. Postgres LISTEN/NOTIFY) has no way
to push a change from one process to another. pos_app, depot_app, and
admin_app are three separate OS processes sharing one file - so when the
POS app deducts stock, depot_app cannot be told about it; it can only
notice by asking again. PollingTimer is that "asking again" mechanic,
extracted once so every screen that needs it (depot_app's low-stock
panel today; admin_app's Inventory Health tab or POS's own alerts,
later) uses the same start/stop/interval-change behavior instead of each
hand-rolling its own QTimer.

This file owns the mechanism only - not what interval to poll at (that's
a per-use-case call, e.g. shared.constants.CRITICAL_STOCK_POLL_INTERVAL_MS)
and not what to do with the result (that's the callback/signal handler).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal


class PollingTimer(QObject):
    """Calls `callback()` immediately and then every `interval_ms`, until stopped.

    Usage:
        self._poller = PollingTimer(get_critical_stock_list, interval_ms=15000, parent=self)
        self._poller.result_ready.connect(self._on_stock_list_updated)
        self._poller.start()

    `callback` runs on the GUI thread via QTimer, so it must be fast
    (a single indexed SELECT, not a slow report query) - see
    database/product_repository.get_critical_stock_list().
    """

    result_ready = Signal(object)

    def __init__(self, callback: Callable[[], object], interval_ms: int, parent: QObject | None = None):
        super().__init__(parent)
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._tick()  # first result arrives immediately, not after one full interval
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_interval_ms(self, interval_ms: int) -> None:
        self._timer.setInterval(interval_ms)

    def _tick(self) -> None:
        # TODO (next slice): decide error handling - a transient DB lock
        # (WAL busy_timeout exhausted under heavy contention) shouldn't
        # crash the poller; likely swallow-and-retry-next-tick with a
        # logged warning, once logging is wired up.
        result = self._callback()
        self.result_ready.emit(result)
