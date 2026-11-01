"""The till's server-sync strip: sends queued sales in the background and says
how many are still waiting (offline) or were refused. Hidden when there is
nothing to say, and always hidden when the till works on its local database."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QLabel, QWidget

from database import remote, remote_sales
from shared.i18n import tr

SYNC_INTERVAL_MS = 20_000


class _Done(QObject):
    finished = Signal(object)


class SyncBanner(QLabel):
    def __init__(self, palette: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setStyleSheet(f"background: {palette.get('warning_bg', '#FFF4D6')}; color: {palette.get('text_primary', '#222')};"
                           " padding: 6px 16px; font-size: 13px;")
        self.hide()
        self._busy = False
        self._done = _Done()
        self._done.finished.connect(self._show)
        self._timer = QTimer(self)
        self._timer.setInterval(SYNC_INTERVAL_MS)
        self._timer.timeout.connect(self.sync_now)
        self._timer.start()
        QTimer.singleShot(0, self.sync_now)

    def sync_now(self) -> None:
        """Send what is queued, off the GUI thread."""
        if not remote.active():
            self.hide()
            return
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                outcome = remote_sales.send_pending()
                outcome["rejected_total"] = len(remote_sales.rejected_sales())
                outcome["online"] = remote.online()
            except Exception:  # a locked database or a bug: a background tick must never kill the till
                outcome = None
            self._done.finished.emit(outcome)

        threading.Thread(target=work, daemon=True).start()

    def _show(self, outcome) -> None:
        self._busy = False
        if outcome is None:
            return
        lines = []
        if outcome["pending"]:
            lines.append(tr("pos.sync.pending").format(n=outcome["pending"]))
        elif not outcome["online"]:
            lines.append(tr("pos.sync.offline"))
        if outcome["rejected_total"]:
            lines.append(tr("pos.sync.rejected").format(n=outcome["rejected_total"]))
        self.setText("\n".join(lines))
        self.setVisible(bool(lines))
