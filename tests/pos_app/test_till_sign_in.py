"""Offscreen tests: the till's cashier sign-in and who rang up a sale."""

from __future__ import annotations

import pytest

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
    employee_repository.create(Employee("M-1", "Murat", "Operations", "Warehouse", "WH-01"))
    account_repository.create_account("M-1", "depot_manager", "7351")


def test_cashiers_and_admins_can_open_the_till_but_not_depot_managers(qapp, cashier):
    from pos_app.gui.auth_flow import till_sign_in_dialog

    dialog = till_sign_in_dialog("001", "Harbor Point")
    dialog.badge_input.setText("M-1")
    dialog.pin_input.setText("7351")
    dialog.try_sign_in()
    assert "can't open the till" in dialog.error_label.text()
    dialog.badge_input.setText("B-2")
    dialog.pin_input.setText("5831")
    dialog.try_sign_in()
    assert dialog.session.name == "Selin Kaya"
    assert account_repository.list_events(1)[0]["terminal"] == "POS 001"


def test_window_shows_the_cashier_and_sales_record_them(qapp, cashier, monkeypatch):
    from pos_app.gui.main_window import MainWindow
    from shared import current_session

    session = account_repository.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    window = MainWindow(session)
    window.show()
    pump(qapp)
    assert window._header._cashier_label.text() == "Selin Kaya"
    assert "Selin" in window._home_page._greeting_label.text()
    assert current_session.actor().label == "Selin Kaya · B-2"

    other = account_repository.authenticate("A-1", "482913", auth.AREA_POS, "POS")
    window.set_session(other)
    assert window._header._cashier_label.text() == "Erol" and current_session.get() == other
    window.close()


def test_a_till_left_alone_is_handed_over_as_at_a_shift_change(qapp, cashier, monkeypatch):
    from pos_app.gui.main_window import MainWindow

    session = account_repository.authenticate("B-2", "5831", auth.AREA_POS, "POS")
    window = MainWindow(session)
    window.show()
    pump(qapp)
    switched = []
    monkeypatch.setattr(window, "switch_cashier", lambda: switched.append(True))
    window._idle_lock.last -= auth.IDLE_LOCK_SECONDS - 60
    assert not window._idle_lock.check() and switched == []
    window._idle_lock.last -= 120
    assert window._idle_lock.check() and switched == [True]
    window._idle_lock.stop()
    window.close()


def test_the_till_reloads_what_is_on_screen_when_the_database_changes(qapp, cashier):
    from pos_app.gui.main_window import MainWindow

    window = MainWindow()
    window._watcher.stop()
    window.show()
    pump(qapp)
    calls = []
    window._home_page.reload_badges = lambda: calls.append("badges")
    window._stock_page.reload = lambda play=True: calls.append(f"stock play={play}")
    window._navigate("stock")
    calls.clear()
    window.refresh_live()
    assert calls == ["badges", "stock play=False"]  # quietly: no replay of the page animation
    window._sale_page.has_items = lambda: True
    sale_reloads = []
    window._sale_page.reload = lambda: sale_reloads.append(1)
    window._navigate("sale")
    sale_reloads.clear()
    window.refresh_live()
    assert sale_reloads == []  # never under a sale being rung up
    window.close()
