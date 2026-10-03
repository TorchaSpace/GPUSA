"""Shared bits for the offscreen GUI tests (tests/admin_app, tests/depot_app).

Sets QT_QPA_PLATFORM=offscreen before anything imports a Qt widget, so
these run on a machine (or CI) with no display, and hands out one
QApplication for the whole session - Qt allows only one per process.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(app) -> None:
    app.processEvents()
    app.processEvents()
