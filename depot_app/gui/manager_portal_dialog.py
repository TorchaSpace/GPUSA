"""Manager Portal modal: the gate behind Console's own "İdari Giriş"
button, recreating the mockup's Purchasing Operations / Local Treasury &
Ledger tabbed dialog with its 10-minute auto-lock countdown and "secure
session" banner.

Both tabs are REAL: "01 Purchasing Operations" is purchasing_panel.py
(raise purchase orders; out-of-range ones are held for approval in
admin_app's Purchase Requests page), "02 Local Treasury & Ledger" is
treasury_panel.py (this depot's checks, notes, payments and receivables
from the shared ledger, settled in admin_app's Treasury page).

The session is real: the Console only opens for a signed-in depot
manager or administrator, and every time this Portal is opened the
Console asks that person's PIN again (depot_app/gui/auth_flow.py). The
header names them; the 10-minute countdown closes the Portal, and the
next opening asks for the PIN again. Purchase orders raised here record
who raised them.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import depot_app.gui.icons as icons
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.gui.purchasing_panel import PurchasingPanel
from depot_app.gui.treasury_panel import TreasuryPanel
from depot_app.theme import FONT_HEADING, INDUSTRY_PALETTE
from shared import current_session
from shared.auth import PORTAL_AUTO_LOCK_SECONDS
from shared.gui_kit.icon_kit import svg_to_icon

_LOCK_SECONDS = PORTAL_AUTO_LOCK_SECONDS


class ManagerPortalDialog(QDialog):
    def __init__(self, site: str, parent: QWidget | None = None):
        """`site` is this depot's "WH-01 · İstanbul Merkez" label - what its
        purchase orders and ledger documents are stamped with."""
        super().__init__(parent)
        p = INDUSTRY_PALETTE
        self.setWindowTitle("Manager Portal")
        self.resize(1080, 820)
        self.setStyleSheet(f"QDialog {{ background-color: {p['background']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(self._build_session_strip())
        layout.addWidget(self._build_header())

        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"""
            QTabWidget::pane {{ border: 1px solid {p['border']}; background-color: {p['surface']}; }}
            QTabBar::tab {{
                background: {p['background']}; color: {p['text_secondary']};
                border: 1px solid {p['border']}; padding: 8px 16px;
                font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 0.5px; font-size: 12px;
            }}
            QTabBar::tab:selected {{ color: {p['accent_900']}; background: {p['accent_100']}; }}
            """
        )
        self._purchasing_panel = PurchasingPanel(site)
        purchasing_inner = QWidget()
        purchasing_layout = QVBoxLayout(purchasing_inner)
        purchasing_layout.setContentsMargins(16, 16, 16, 16)
        purchasing_layout.addWidget(self._purchasing_panel)
        purchasing_scroll = QScrollArea()
        purchasing_scroll.setWidgetResizable(True)
        purchasing_scroll.setFrameShape(QScrollArea.NoFrame)
        purchasing_scroll.setWidget(purchasing_inner)
        tabs.addTab(purchasing_scroll, "01  Purchasing Operations")

        self._treasury_panel = TreasuryPanel(site)
        treasury_inner = QWidget()
        treasury_layout = QVBoxLayout(treasury_inner)
        treasury_layout.setContentsMargins(24, 24, 24, 24)
        treasury_layout.addWidget(self._treasury_panel)
        treasury_scroll = QScrollArea()
        treasury_scroll.setWidgetResizable(True)
        treasury_scroll.setFrameShape(QScrollArea.NoFrame)
        treasury_scroll.setWidget(treasury_inner)
        tabs.addTab(treasury_scroll, "02  Local Treasury && Ledger")

        layout.addWidget(tabs, stretch=1)

        self._remaining_seconds = _LOCK_SECONDS
        self._lock_timer = QTimer(self)
        self._lock_timer.setInterval(1000)
        self._lock_timer.timeout.connect(self._tick_lock)
        self._lock_timer.start()
        self._update_lock_label()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Each opening is a fresh "session": restart the auto-lock
        # countdown (previously it stayed expired after the first
        # auto-lock), and re-read products/bands an admin may have changed.
        self._remaining_seconds = _LOCK_SECONDS
        self._update_lock_label()
        self._lock_timer.start()
        self._update_identity()
        self._purchasing_panel.reload_catalog()
        self._purchasing_panel.start_polling()
        self._treasury_panel.reload()  # Admin may have settled documents since

    def hideEvent(self, event) -> None:
        self._purchasing_panel.stop_polling()
        super().hideEvent(event)

    def _build_session_strip(self) -> QWidget:
        p = INDUSTRY_PALETTE
        strip = QWidget()
        # Scoped to the strip itself: a selector-less stylesheet cascades to
        # every child, which drew a box around each label inside it.
        strip.setObjectName("sessionStrip")
        strip.setAttribute(Qt.WA_StyledBackground, True)
        strip.setStyleSheet(f"#sessionStrip {{ background-color: {p['accent_100']}; border: 1px solid {p['accent']}; }}")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(svg_to_icon(icons.SHIELD, p["accent_900"], size=14).pixmap(14, 14))
        layout.addWidget(icon)

        text = QLabel("Secure session · Financial data · Not visible on floor terminals")
        text.setStyleSheet(f"font-size: 11px; color: {p['accent_900']};")
        layout.addWidget(text)
        layout.addStretch(1)

        self._lock_label = QLabel()
        self._lock_label.setStyleSheet(f"font-size: 11px; color: {p['accent_900']}; font-weight: 600;")
        layout.addWidget(self._lock_label)
        return strip

    def _build_header(self) -> QWidget:
        p = INDUSTRY_PALETTE
        header = QHBoxLayout()

        titles = QVBoxLayout()
        titles.setSpacing(2)
        kicker = QLabel("İDARİ GİRİŞ")
        self._kicker_label = kicker
        kicker.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        titles.addWidget(kicker)
        title = QLabel("MANAGER PORTAL")
        title.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 600; letter-spacing: 1px; "
            f"font-size: 24px; color: {p['text_primary']};"
        )
        titles.addWidget(title)
        subtext = QLabel()
        self._subtext_label = subtext
        subtext.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        titles.addWidget(subtext)
        titles_widget = QWidget()
        titles_widget.setLayout(titles)
        header.addWidget(titles_widget, stretch=1)

        lock_exit = IndustryButton("Lock && exit", variant="ghost")
        lock_exit.clicked.connect(self.close)
        header.addWidget(lock_exit, alignment=Qt.AlignTop)

        widget = QWidget()
        widget.setLayout(header)
        return widget

    def _update_identity(self) -> None:
        """Name the signed-in person (the Console's session) and when this
        Portal session started."""
        from datetime import datetime

        session = current_session.get()
        who = f"{session.name} · {session.role_label}" if session else "Not signed in"
        self._kicker_label.setText(f"İDARİ GİRİŞ · {session.role_label.upper()}" if session else "İDARİ GİRİŞ")
        self._subtext_label.setText(f"{who} · unlocked {datetime.now().strftime('%H:%M')}")

    def _tick_lock(self) -> None:
        self._remaining_seconds -= 1
        if self._remaining_seconds <= 0:
            self._lock_timer.stop()
            self.close()
            return
        self._update_lock_label()

    def _update_lock_label(self) -> None:
        minutes, seconds = divmod(self._remaining_seconds, 60)
        self._lock_label.setText(f"Auto-lock in {minutes}:{seconds:02d}")
