"""Admin > Server (root only): the server this computer works with, at a glance.

Nothing to sign in to here and nothing to switch on: the apps find the server when they start and the
badge + PIN typed at sign-in is the connection (database/server_discovery.py, account_repository.authenticate).
The page only shows it - address, whether it answers, its version and database, who is signed in, how much it
holds - and lets root type another address for this computer (used from the next start on).

Every request runs in a background thread, so a slow or unreachable server never freezes the window.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.gui.restart import restart_app
from admin_app.theme import CLASSICAL_PALETTE
from database import remote
from database.exceptions import DATABASE_ERRORS
from shared import auth, current_session, discovery, paths
from shared.i18n import tr
from shared.gui_kit.server_line import why_not
from shared.server_client import ServerClient, ServerError, normalize_url

COUNTS = (("products", "/products"), ("dealerships", "/dealerships"), ("warehouses", "/warehouses"),
          ("employees", "/employees"), ("accounts", "/accounts"))


class _Result(QObject):
    """Carries a background result back to the GUI thread."""

    done = Signal(object)


def read_server(url: str, signed_in: bool) -> dict:
    """What the page shows about the server at `url`. Never raises: what could not be read stays None."""
    info = {"url": url, "reachable": False, "version": None, "database": None, "counts": {}, "error": None}
    plain = ServerClient(url, timeout=4.0)
    try:
        health = plain.get("/health") or {}
    except ServerError as exc:
        info["error"] = str(exc)
        return info
    info["reachable"] = health.get("status") == "ok"
    info["version"] = health.get("version")
    try:
        info["database"] = (plain.get("/health/db") or {}).get("database") == "ok"
    except ServerError:
        info["database"] = False
    if signed_in:
        for key, url_path in COUNTS:
            try:
                info["counts"][key] = len(remote.call("GET", url_path))
            except DATABASE_ERRORS:
                info["counts"][key] = None
    return info


class ServerPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("admin.server.title"), parent, subtitle=tr("admin.server.subtitle"))
        p = CLASSICAL_PALETTE
        self._busy = False
        self._result = _Result()
        self._result.done.connect(self._on_done)

        self.refresh_button = CompactButton(tr("admin.refresh"))
        self.refresh_button.clicked.connect(self.reload)
        self.add_header_action(self.refresh_button)

        # --- the connection --------------------------------------------------
        connection = Section(tr("admin.server.kicker"), tr("admin.server.connection"))
        form = QFormLayout()
        form.setContentsMargins(16, 12, 16, 14)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(8)
        self.values: dict[str, QLabel] = {}
        for key in ("address", "status", "version", "database", "signed_in", "holds"):
            caption = QLabel(tr(f"admin.server.{key}"))
            caption.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
            value = QLabel("—")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
            form.addRow(caption, value)
            self.values[key] = value
        host = QWidget()
        host.setLayout(form)
        connection.body_layout().addWidget(host)
        self.body_layout().addWidget(connection)

        # --- another address for this computer -------------------------------
        change = Section(tr("admin.server.kicker"), tr("admin.server.change"))
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(16, 12, 16, 6)
        row.setSpacing(8)
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText(tr("server.line.placeholder"))
        self.address_edit.returnPressed.connect(self.save_address)
        self.save_button = CompactButton(tr("common.save"))
        self.save_button.clicked.connect(self.save_address)
        self.find_button = CompactButton(tr("admin.server.find"))
        self.find_button.clicked.connect(self.find_on_network)
        self.restart_button = CompactButton(tr("admin.server.restart"))
        self.restart_button.clicked.connect(restart_app)
        self.restart_button.hide()
        row.addWidget(self.address_edit, 1)
        row.addWidget(self.save_button)
        row.addWidget(self.find_button)
        row.addWidget(self.restart_button)
        change.body_layout().addWidget(box)
        self.change_message = QLabel(tr("admin.server.change_hint"))
        self.change_message.setWordWrap(True)
        self.change_message.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; padding: 0 16px 12px 16px;")
        change.body_layout().addWidget(self.change_message)
        self.body_layout().addWidget(change)
        self.body_layout().addStretch(1)
        self._show_static()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()

    # --- what is known without asking the server ---------------------------------

    def _url(self) -> str | None:
        return remote.server_url() or paths.get_server_url()

    def _show_static(self) -> None:
        url = self._url()
        self.values["address"].setText(url or tr("admin.server.no_address"))
        session = current_session.get()
        self.values["signed_in"].setText(
            tr("admin.server.signed_in_as").format(
                name=session.name, badge=session.badge_id,
                role=auth.role_label("root" if session.is_root else session.role)) if session else "—")
        if not url:
            self._set_status(tr("admin.server.local_only"), "text_secondary")
            for key in ("version", "database", "holds"):
                self.values[key].setText("—")

    def _set_status(self, text: str, color: str) -> None:
        self.values["status"].setText(text)
        self.values["status"].setStyleSheet(f"font-size: 13px; color: {CLASSICAL_PALETTE[color]};")

    # --- asking the server --------------------------------------------------------

    def reload(self) -> None:
        self._show_static()
        url = self._url()
        if self._busy or not url:
            return
        self._busy = True
        self.refresh_button.setEnabled(False)
        self._set_status(tr("admin.server.checking"), "text_secondary")
        signed_in = remote.active()

        def target():
            try:
                outcome = read_server(url, signed_in)
            except Exception as exc:  # never let a bug kill the thread silently
                outcome = {"url": url, "reachable": False, "version": None, "database": None, "counts": {},
                           "error": str(exc)}
            self._result.done.emit(outcome)

        threading.Thread(target=target, daemon=True).start()

    def _on_done(self, info: dict) -> None:
        self._busy = False
        self.refresh_button.setEnabled(True)
        if not info["reachable"]:
            self._set_status(tr("admin.server.unreachable").format(error=info["error"] or ""), "alert_critical")
            return
        self._set_status(tr("admin.server.connected"), "alert_success")
        self.values["version"].setText(info["version"] or "—")
        self.values["database"].setText(tr("admin.server.db_ok") if info["database"] else tr("admin.server.db_down"))
        counts = info["counts"]
        self.values["holds"].setText(tr("admin.server.summary").format(
            **{key: "-" if counts.get(key) is None else counts[key] for key, _path in COUNTS}) if counts else "—")

    # --- another address ------------------------------------------------------------

    def _say(self, text: str, problem: bool = False) -> None:
        color = CLASSICAL_PALETTE["alert_critical" if problem else "text_secondary"]
        self.change_message.setText(text)
        self.change_message.setStyleSheet(f"font-size: 12px; color: {color}; padding: 0 16px 12px 16px;")

    def find_on_network(self) -> None:
        try:
            url = discovery.find_server()
        except OSError:
            url = None
        if url:
            self.address_edit.setText(url)
            self._say(tr("admin.server.found").format(url=url))
        else:
            self._say(tr("admin.server.not_found"), problem=True)

    def save_address(self) -> bool:
        """Save the typed address for every app on this computer, once a GPUSA server answers there. An
        empty box takes this computer off the server (back to its own data file). Used from the next start."""
        typed = normalize_url(self.address_edit.text())
        url = discovery.reach(typed, 4.0) if typed else ""
        if typed and not url:
            self._say(why_not(typed), problem=True)
            return False
        if not url and not paths.get_server_url():
            return False
        discovery.save_server(url or None, typed=True)  # typed on purpose: the next sign-in pairs with that server
        self.address_edit.clear()
        self._say(tr("admin.server.saved").format(url=url) if url else tr("admin.server.cleared"))
        self.restart_button.show()
        return True
