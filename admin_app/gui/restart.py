"""Relaunch Admin (used after the interface language changes: every label is
built once at start-up, so the clean way to switch language is to start
over)."""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication


def relaunch_command() -> list[str]:
    """Program and arguments that start this same Admin again: the packaged
    app is its own executable; a source run is `python <script> ...`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, *sys.argv]


def restart_app() -> bool:
    """Start a fresh Admin and quit this one. False if it could not start
    (the current window then simply stays open)."""
    program, *arguments = relaunch_command()
    if not QProcess.startDetached(program, arguments):
        return False
    app = QApplication.instance()
    if app is not None:
        app.quit()
    return True
