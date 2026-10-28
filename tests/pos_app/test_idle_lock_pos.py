"""POS idle lock: fires even over an open dialog, and never closes the sign-in dialog."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import account_repository, employee_repository
from shared import auth
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def cashier():
    account_repository.create_first_admin("A-1", "Erol", "482913")
    employee_repository.create(Employee("B-2", "Selin Kaya", "Sales & service", "Dealership", "Harbor Point"))
    account_repository.create_account("B-2", "cashier", "5831")


class _Dialog:
    """Stands in for the sign-in dialog: records that it was shown, then 'cancels'."""
    def __init__(self, on_exec=None):
        self.session = None
        self.on_exec = on_exec

    def exec(self):
        if self.on_exec:
            self.on_exec()
        return 0


def test_idle_lock_closes_an_open_dialog_and_hands_the_till_over(qapp, cashier, monkeypatch):
    import pos_app.gui.main_window as mw

    session = account_repository.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    window = mw.MainWindow(session)
    window._watcher.stop()
    window.show()
    pump(qapp)
    box = QMessageBox(window)
    box.setModal(True)
    box.show()
    pump(qapp)
    assert QApplication.activeModalWidget() is box
    quit_calls = []
    monkeypatch.setattr(mw, "till_sign_in_dialog", lambda *a, **k: _Dialog())
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quit_calls.append(1)))
    window._idle_lock.last -= auth.IDLE_LOCK_SECONDS + 60
    assert window._idle_lock.check()
    assert window.session is None
    assert not box.isVisible()
    window._idle_lock.stop()
    window.close()


def test_idle_lock_does_not_close_the_sign_in_dialog(qapp, cashier, monkeypatch):
    import pos_app.gui.main_window as mw

    session = account_repository.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    window = mw.MainWindow(session)
    window._watcher.stop()
    window.show()
    pump(qapp)
    window.session = None  # the sign-in dialog is up
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quit_calls.append(1)))
    monkeypatch.setattr(mw, "till_sign_in_dialog", lambda *a, **k: pytest.fail("must not re-open sign-in"))
    window._idle_lock.last -= auth.IDLE_LOCK_SECONDS + 60
    assert window._idle_lock.check()
    assert quit_calls == []
    window._idle_lock.stop()
    window.close()
