"""IdleLock: notices when nobody has touched the app for a while.

Admin and POS sign whoever is at the screen out after IDLE_LOCK_SECONDS
without mouse / keyboard / touch input (the depot Console has its own,
older copy of the same idea in depot_app/gui/console_window.py). This file
owns only the "how long since the last input" mechanic; what locking
means is the window's call - Admin re-asks for a sign-in, POS hands the
till over as at a shift change.

It never fires while a modal dialog is open: locking would re-enter the
window's own sign-in flow underneath whatever that dialog is doing. The
dialog closing (or any input) restarts the clock, so the lock follows
right after.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

_INPUT = {QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.KeyPress, QEvent.Wheel, QEvent.TouchBegin}
_CHECK_MS = 15_000


class IdleLock(QObject):
    """Emits `idle` once nothing has been typed or clicked for `seconds`.

    Usage:
        self._idle = IdleLock(IDLE_LOCK_SECONDS, parent=self)
        self._idle.idle.connect(self.sign_out)
        self._idle.start()
    """

    idle = Signal()

    def __init__(self, seconds: float, parent: QObject | None = None,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        self.seconds = seconds
        self._clock = clock
        self.last = clock()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)

    def start(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.touch()
        self._timer.start(_CHECK_MS)

    def stop(self) -> None:
        self._timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def touch(self) -> None:
        """Count now as activity (input, or a fresh sign-in)."""
        self.last = self._clock()

    def eventFilter(self, obj, event) -> bool:
        if event.type() in _INPUT:
            self.last = self._clock()
        return False

    def check(self) -> bool:
        """Emit `idle` (and restart the clock) if the limit has passed.
        Returns whether it fired."""
        if QApplication.activeModalWidget() is not None:
            return False
        if self._clock() - self.last <= self.seconds:
            return False
        self.touch()  # one lock per idle stretch, not one every check
        self.idle.emit()
        return True
