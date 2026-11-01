"""Admin > Server (test): sign in to the cloud API and list what it holds.

A safe first step towards the server: it does NOT replace any page and
changes nothing, here or there. It signs in with a badge and PIN, then shows
the products, plus how many dealerships, warehouses and employees the server
has, so you can see that Admin and the server talk to each other. Every
request runs in a background thread (a slow or unreachable server never
freezes the window); the token lives in memory only and the PIN is cleared
as soon as the sign-in has been sent.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.theme import CLASSICAL_PALETTE
from database import remote
from shared.i18n import tr
from shared.server_client import ServerClient, ServerError

DEFAULT_URL = "http://localhost:8000"


class _Result(QObject):
    """Carries a background result back to the GUI thread."""

    done = Signal(object)


class ServerTestPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("admin.server_test.title"), parent, subtitle=tr("admin.server_test.subtitle"))
        p = CLASSICAL_PALETTE
        self._client: ServerClient | None = None
        self._busy = False
        self._result = _Result()
        self._result.done.connect(self._on_done)

        form = QWidget()
        row = QHBoxLayout(form)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.url_edit = QLineEdit(os.environ.get("GPUSA_API_URL") or DEFAULT_URL)
        self.url_edit.setPlaceholderText(tr("admin.server_test.url"))
        self.badge_edit = QLineEdit()
        self.badge_edit.setPlaceholderText(tr("admin.auth.badge"))
        self.pin_edit = QLineEdit()
        self.pin_edit.setPlaceholderText(tr("admin.server_test.pin"))
        self.pin_edit.setEchoMode(QLineEdit.Password)
        self.connect_button = QPushButton(tr("admin.server_test.connect"))
        self.connect_button.clicked.connect(self.connect_to_server)
        self.pin_edit.returnPressed.connect(self.connect_to_server)
        self.refresh_button = QPushButton(tr("admin.server_test.refresh"))
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.load_data)
        row.addWidget(self.url_edit, 3)
        row.addWidget(self.badge_edit, 1)
        row.addWidget(self.pin_edit, 1)
        row.addWidget(self.connect_button)
        row.addWidget(self.refresh_button)
        self.body_layout().addWidget(form)

        self.status_label = QLabel(tr("admin.server_test.not_connected"))
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        self.body_layout().addWidget(self.status_label)

        self.server_mode = QCheckBox(tr("admin.server_test.server_mode"))
        self.server_mode.setEnabled(False)
        self.server_mode.setToolTip(tr("admin.server_test.server_mode_hint"))
        self.server_mode.toggled.connect(self._toggle_server_mode)
        self.body_layout().addWidget(self.server_mode)
        self.mode_label = QLabel("")
        self.mode_label.setWordWrap(True)
        self.mode_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        self.body_layout().addWidget(self.mode_label)
        self._show_mode()

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        self.body_layout().addWidget(self.summary_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            tr("admin.server_test.col_barcode"), tr("admin.server_test.col_name"),
            tr("admin.server_test.col_price"), tr("admin.server_test.col_stock"),
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.body_layout().addWidget(self.table, 1)

    # --- actions -------------------------------------------------------------

    def _run(self, work) -> None:
        """Run `work()` off the GUI thread; its value (or the ServerError) comes back to _on_done."""
        self._busy = True
        self.connect_button.setEnabled(False)
        self.refresh_button.setEnabled(False)

        def target():
            try:
                outcome = work()
            except ServerError as exc:
                outcome = exc
            except Exception as exc:  # never let a bug kill the thread silently
                outcome = ServerError(str(exc))
            self._result.done.emit(outcome)

        threading.Thread(target=target, daemon=True).start()

    def connect_to_server(self) -> None:
        if self._busy:
            return
        client = ServerClient(self.url_edit.text())
        badge, pin = self.badge_edit.text(), self.pin_edit.text()
        self.pin_edit.clear()  # the PIN is not kept
        self.status_label.setText(tr("admin.server_test.connecting"))

        def work():
            who = client.login(badge, pin)
            return ("login", client, who, self._fetch_all(client))

        self._run(work)

    def load_data(self) -> None:
        if self._busy or self._client is None:
            return
        client = self._client
        self.status_label.setText(tr("admin.server_test.connecting"))
        self._run(lambda: ("data", client, None, self._fetch_all(client)))

    @staticmethod
    def _fetch_all(client: ServerClient) -> dict:
        data = {"products": client.get("/products"),
                "dealerships": len(client.get("/dealerships")),
                "warehouses": len(client.get("/warehouses"))}
        try:
            data["employees"] = len(client.get("/employees"))
        except ServerError as exc:
            if exc.status != 403:
                raise
            data["employees"] = None
        return data

    def _on_done(self, outcome) -> None:
        self._busy = False
        self.connect_button.setEnabled(True)
        if isinstance(outcome, ServerError):
            self.status_label.setText(tr("admin.server_test.failed").format(error=str(outcome)))
            self.refresh_button.setEnabled(self._client is not None)
            return
        kind, client, who, data = outcome
        if kind == "login" and remote.active():
            remote.use_server(client)  # signed in again: keep server mode on the new sign-in
        self._client = client
        self.refresh_button.setEnabled(True)
        self.server_mode.setEnabled(True)
        if kind == "login":
            self.status_label.setText(tr("admin.server_test.connected").format(
                name=who["name"], badge=who["badge_id"], url=client.base_url))
        else:
            self.status_label.setText(tr("admin.server_test.refreshed").format(url=client.base_url))
        self._show(data)

    def _show(self, data: dict) -> None:
        products = data["products"]
        self.table.setRowCount(len(products))
        for r, product in enumerate(products):
            cells = [product["barcode"], product["name"], f"{product['price']:.2f}", str(product["stock_quantity"])]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)
        employees = data["employees"]
        self.summary_label.setText(tr("admin.server_test.summary").format(
            products=len(products), dealerships=data["dealerships"], warehouses=data["warehouses"],
            employees="-" if employees is None else employees))

    # --- server mode ---------------------------------------------------------

    def _toggle_server_mode(self, on: bool) -> None:
        """On: Products (list, add, edit, cost, on/off) and stock in/out use this
        server until switched off or Admin closes. Off: back to this computer's data."""
        remote.use_server(self._client if on and self._client is not None else None)
        self._show_mode()

    def _show_mode(self) -> None:
        if remote.active():
            self.mode_label.setText(tr("admin.server_test.mode_server").format(url=remote.server_url()))
        else:
            self.mode_label.setText(tr("admin.server_test.mode_local"))
