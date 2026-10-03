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
