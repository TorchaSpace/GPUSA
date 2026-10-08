"""shared.gui_kit.idle_lock: fires once per idle stretch, never under a modal dialog."""

from __future__ import annotations

from tests.gui_support import pump, qapp  # noqa: F401

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog

from shared.gui_kit.idle_lock import IdleLock


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_fires_after_the_limit_and_only_once_per_idle_stretch(qapp):
    clock = _Clock()
    lock = IdleLock(60, clock=clock)
    fired = []
    lock.idle.connect(lambda: fired.append(True))
    clock.now += 59
    assert not lock.check()
    clock.now += 2
    assert lock.check() and fired == [True]
    assert not lock.check()  # the clock restarted; no second lock straight away
    lock.touch()
    clock.now += 30
    assert not lock.check() and fired == [True]


def test_input_restarts_the_clock(qapp):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import Qt

    clock = _Clock()
    lock = IdleLock(60, clock=clock)
    clock.now += 50
    lock.eventFilter(None, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier))
    clock.now += 50
    assert not lock.check()


def test_never_fires_under_a_modal_dialog(qapp):
    clock = _Clock()
    lock = IdleLock(60, clock=clock)
    clock.now += 120
    seen = []

    def probe():
        seen.append(lock.check())
        dialog.reject()

    dialog = QDialog()
    QTimer.singleShot(0, probe)
    dialog.exec()
    assert seen == [False]
    assert lock.check()
