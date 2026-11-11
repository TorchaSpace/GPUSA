"""One quiet line on the sign-in screens about the server, so connecting never needs a separate page.

The apps find the server by themselves when they start (database/server_discovery.py). This line only says
what happened and offers the one thing that can still be needed: typing the address when the server is not
on this network (or moved). The saved address is never shown here - only root sees it, in Admin > Server.

The address typed is believed only after a GPUSA API answers there; then it is saved for every app on this
computer and `changed` is emitted (the sign-in that follows goes to that server).
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from shared import discovery, paths
from shared.i18n import tr
from shared.server_client import insecure_problem, normalize_url


def why_not(typed: str) -> str:
    """What to tell someone whose typed address got no usable answer."""
    if discovery.only_plain_http_on_internet(typed):
        return tr("server.line.plain_http_internet")
    if insecure_problem(typed):
        return tr("server.line.insecure")
    return tr("server.line.no_answer").format(url=typed)


class ServerLine(QWidget):
    changed = Signal(str)  # the address just saved

    def __init__(self, palette: dict, parent: QWidget | None = None):
        super().__init__(parent)
        p = palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.status = QLabel()
        self.status.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        self.toggle = QPushButton()
        self.toggle.setFlat(True)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self.toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; color: {p['accent']}; padding: 0; font-size: 12px; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}")
        self.toggle.clicked.connect(self._toggle_form)
        top.addWidget(self.status)
        top.addWidget(self.toggle)
        top.addStretch(1)
        layout.addLayout(top)

        self._form = QWidget()
        row = QHBoxLayout(self._form)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.address = QLineEdit()
        self.address.setPlaceholderText(tr("server.line.placeholder"))
        self.address.returnPressed.connect(self.connect_to)
        self.connect_button = QPushButton(tr("server.line.connect"))
        self.connect_button.setAutoDefault(False)
        self.connect_button.clicked.connect(self.connect_to)
        row.addWidget(self.address, 1)
        row.addWidget(self.connect_button)
        self._form.hide()
        layout.addWidget(self._form)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setStyleSheet(f"font-size: 12px; color: {p['alert_critical']};")
        self.message.hide()
        layout.addWidget(self.message)
        self._show_state()

    def _show_state(self) -> None:
        connected = bool(paths.get_server_url())
        self.status.setText(tr("server.line.connected") if connected else tr("server.line.none"))
        self.toggle.setText(tr("server.line.change") if connected else tr("server.line.enter"))

    def _toggle_form(self) -> None:
        self._form.setVisible(not self._form.isVisible())
        self.message.hide()
        if self._form.isVisible():
            self.address.setFocus()
        self._resize_window()

    def _resize_window(self) -> None:
        window = self.window()
        if window is not None and window is not self:
            window.adjustSize()

    def connect_to(self) -> bool:
        """Check the typed address and save it. False (with the reason shown) when no GPUSA server answers."""
        typed = normalize_url(self.address.text())
        if not typed:
            return False
        self.connect_button.setEnabled(False)
        try:
            url = discovery.reach(typed, 4.0)
        finally:
            self.connect_button.setEnabled(True)
        if not url:
            self.message.setText(why_not(typed))
            self.message.show()
            self._resize_window()
            return False
        fixed = normalize_url(os.environ.get(paths.SERVER_URL_ENV) or "")
        if fixed and url != fixed:
            # GPUSA_SERVER_URL wins over anything saved here; only that same server can be confirmed again.
            self.message.setText(tr("server.line.env_fixed"))
            self.message.show()
            self._resize_window()
            return False
        paths.set_server_url(url)
        paths.set_server_pairing(None)  # typed on purpose: the next sign-in pairs with this server
        self.address.clear()
        self._form.hide()
        self.message.hide()
        self._show_state()
        self._resize_window()
        self.changed.emit(url)
        return True
