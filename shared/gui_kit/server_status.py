"""A strip for Admin and the Depot: shown while the server cannot be reached.

Admin and the Depot never write offline (only the till queues sales): without the server they show the
last data they received and every change is refused. This strip says so, so a refused change is not a
surprise, and it goes away by itself when the server answers again. Hidden when the app works on its
local database."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QWidget

from database import remote
from shared.i18n import tr

CHECK_INTERVAL_MS = 15_000


class _Done(QObject):
    finished = Signal(bool)


class ServerStatusStrip(QLabel):
    def __init__(self, palette: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setStyleSheet(f"background: {palette.get('warning_bg', '#FFF4D6')}; "
                           f"color: {palette.get('text_primary', '#222')}; padding: 6px 16px; font-size: 13px;")
        self.setText(tr("server.read_only_offline"))
        self.hide()
        self._busy = False
        self._done = _Done()
        self._done.finished.connect(self._show)
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)
        self._timer.start()
        QTimer.singleShot(0, self.check_now)

    def check_now(self) -> None:
        """Ask the server (off the GUI thread: an unreachable one takes a network timeout to answer)."""
        if not remote.active():
            self.hide()
            return
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                online = remote.ping()
            except Exception:  # a background tick must never take the app down
                online = True
            self._done.finished.emit(online)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, online: bool) -> None:
        self._busy = False
        self.setVisible(remote.active() and not online)
