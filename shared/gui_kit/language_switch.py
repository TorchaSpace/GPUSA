"""A tiny "TR | EN" switch for a corner of a window.

Picking the other language saves it (the same store setting Admin > Settings
uses) and restarts the app, because every screen builds its texts once at
start-up. `restart` and `save` are injectable so tests never relaunch anything."""

from __future__ import annotations

import sys
from typing import Callable

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QWidget

from shared import i18n

CODES = (("tr", "TR"), ("en", "EN"))


def restart_app() -> None:
    """Start a fresh copy of this program, then close this one."""
    if getattr(sys, "frozen", False):
        program, args = sys.executable, sys.argv[1:]
    else:
        program, args = sys.executable, sys.argv
    QProcess.startDetached(program, args)
    QApplication.quit()


def save_language(code: str) -> None:
    from database import settings_repository

    settings_repository.save_language(code)


class LanguageSwitch(QWidget):
    def __init__(self, palette: dict, parent: QWidget | None = None,
                 restart: Callable[[], None] = restart_app,
                 save: Callable[[str], None] = save_language, rounded: bool = False):
        super().__init__(parent)
        self._restart = restart
        self._save = save
        self._buttons: dict[str, QPushButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for code, text in CODES:
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedSize(38, 28)
            button.clicked.connect(lambda _checked=False, c=code: self.choose(c))
            layout.addWidget(button)
            self._buttons[code] = button
        self._palette = palette
        self._rounded = rounded
        self.refresh()

    def refresh(self) -> None:
        p = self._palette
        current = i18n.current_language()
        codes = list(self._buttons)
        for code, button in self._buttons.items():
            on = code == current
            if self._rounded:  # a pill split in two: round only the outer corners
                first, last = code == codes[0], code == codes[-1]
                corners = f"border-top-left-radius: {14 if first else 0}px; border-bottom-left-radius: {14 if first else 0}px; " \
                          f"border-top-right-radius: {14 if last else 0}px; border-bottom-right-radius: {14 if last else 0}px;"
            else:
                corners = "border-radius: 0;"
            button.setStyleSheet(
                f"QPushButton {{ border: 1px solid {p['border']}; {corners} font-size: 12px; font-weight: 700; "
                f"letter-spacing: 1px; background-color: {p['text_primary'] if on else 'transparent'}; "
                f"color: {'#ffffff' if on else p['text_secondary']}; }}"
                f"QPushButton:hover {{ border-color: {p['text_primary']}; }}"
            )

    def choose(self, code: str) -> None:
        if code == i18n.current_language():
            return
        try:
            self._save(code)
        except Exception:  # a locked database must not strand the person - they can try again
            return
        self._restart()
