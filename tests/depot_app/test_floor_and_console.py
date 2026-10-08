"""Offscreen tests: the depot Floor and Console work on THIS depot's warehouse."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import product_repository, stock_repository, warehouse_repository
from shared.auth import Session
from shared.models import UNASSIGNED, Product, StockLocation, Warehouse

WH1, WH2 = StockLocation.warehouse("WH-01"), StockLocation.warehouse("WH-02")
MANAGER = Session(1, "M-1", "Test Manager", "depot_manager", "2026-09-26T06:00:00.000Z", "depot_console", "Depot WH-01")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def floor(qapp):
    here = warehouse_repository.create(Warehouse(code="WH-01", name="İstanbul Merkez", capacity_units=100, docks=4))
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze"))
    product_repository.create(Product("BOX", "Carton", 40, 30, 10))
    stock_repository.transfer(UNASSIGNED, WH2, "BOX", 30)  # all of it is at the OTHER warehouse
    from depot_app.gui.main_window import MainWindow

    window = MainWindow(here)
    window.show()
    pump(qapp)
    yield window
    if window._console_window is not None:
        window._console_window.close()
    window.close()


def test_floor_is_labelled_with_its_warehouse(floor):
    assert floor.windowTitle() == "Dockline Floor · WH-01 · İstanbul Merkez"


def test_low_stock_banner_reads_this_warehouse(floor):
    banner = floor._low_stock_banner
    # BOX: 30 in the company, all at the OTHER warehouse. This one never carried it, so there
    # is nothing here to reorder.
    banner.reload()
    assert not banner._card.isVisibleTo(floor)
    # Once this warehouse has held it and is at/below the reorder level, it is flagged.
    stock_repository.receive(WH1, "BOX", 3)
    banner.reload()
    assert banner._card.isVisibleTo(floor)


def test_inbound_and_outbound_move_this_warehouse_only(floor):
    inbound, outbound = floor._receive_panel, floor._dispatch_panel
    inbound._sku_input.setText("box")
    inbound._qty_input.setValue(12)
    inbound._on_submit()
    assert stock_repository.quantity_at(WH1, "BOX") == 12
    assert stock_repository.quantity_at(WH2, "BOX") == 30

    outbound._sku_input.setText("BOX")
    outbound._qty_input.setValue(13)
    outbound._on_submit()
    assert "Only 12 on hand at WH-01" in outbound._error_label.text()
    assert stock_repository.quantity_at(WH1, "BOX") == 12


def test_console_cards_and_log(floor, qapp):
    stock_repository.receive(WH1, "BOX", 90)  # 90 / 100 -> near capacity
    floor._open_console(MANAGER)
    console = floor._console_window
    assert console._who_label.text() == "Test Manager"
    pump(qapp)
    codes = [c.warehouse.code for c in console._cards]
    assert codes == ["WH-01", "WH-02"]  # this depot first
    mine = console._cards[0]
    assert mine.status_label.text() == "NEAR CAPACITY"
    assert mine.usage_label.text() == "90 / 100 units · 90%"
    assert console._movements_table.rowCount() == 1
    console._select_warehouse("WH-02")
    assert console._movements_table.item(0, 5).text() == "Transfer"
    assert console._unassigned_note.isHidden()


def test_console_sign_out_hides_it_and_clears_the_session(floor, qapp):
    from shared import current_session

    floor._open_console(MANAGER)
    console = floor._console_window
    assert current_session.get() == MANAGER
    console.sign_out()
    assert current_session.get() is None and not console.isVisible() and console.session is None


def test_floor_movements_are_not_attributed_to_the_console_manager(floor, qapp):
    floor._open_console(MANAGER)  # a manager is signed in on this PC...
    floor._receive_panel._sku_input.setText("BOX")
    floor._receive_panel._qty_input.setValue(1)
    floor._receive_panel._on_submit()  # ...but the Floor is an open kiosk
    assert stock_repository.list_movements(1)[0]["handled_by"] is None


def test_inbound_refuses_a_deactivated_product_with_a_clear_message(floor):
    product_repository.set_active("BOX", False)
    inbound = floor._receive_panel
    inbound._sku_input.setText("BOX")
    inbound._qty_input.setValue(1)
    inbound._on_submit()
    assert "deactivated" in inbound._error_label.text()
    assert stock_repository.quantity_at(WH1, "BOX") == 0


def test_inbound_past_capacity_is_a_message_not_a_crash(floor):
    inbound = floor._receive_panel
    inbound._sku_input.setText("BOX")
    inbound._qty_input.setValue(101)  # WH-01 holds 0 of 100
    inbound._on_submit()
    assert "exceed" in inbound._error_label.text()
    assert stock_repository.quantity_at(WH1, "BOX") == 0


def _worker():
    from database import attendance_repository, employee_repository
    from shared.models import Employee

    employee_repository.create(Employee("W-1", "Ayşe Kaya", "Operations", "Warehouse", "WH-01"))
    attendance_repository.check_in("W-1")


def test_floor_movement_is_credited_to_a_checked_in_operator_with_ref_and_bin(floor):
    _worker()
    inbound = floor._receive_panel
    inbound._sku_input.setText("BOX")
    inbound._qty_input.setValue(5)
    inbound._ref_input.setText("PO-77")
    inbound._loc_input.setText("A-03")
    inbound._operator_input.setText("w-1")
    inbound._on_submit()
    move = stock_repository.list_movements(1)[0]
    assert (move["reference"], move["bin_code"], move["handled_by"]) == ("PO-77", "A-03", "Ayşe Kaya · W-1")
    assert inbound._table.item(0, 3).text() == "PO-77" and inbound._table.item(0, 4).text() == "A-03"
    assert inbound._table.item(0, 5).text() == "Ayşe Kaya"
    assert inbound._operator_input.text() == "w-1"  # stays filled for the next scan


def test_unknown_or_checked_out_operator_is_refused(floor):
    inbound = floor._receive_panel
    inbound._sku_input.setText("BOX")
    inbound._operator_input.setText("NOPE")
    inbound._on_submit()
    assert inbound._error_label.text()
    assert stock_repository.quantity_at(WH1, "BOX") == 0


def test_same_scan_twice_in_a_blink_is_logged_once(floor):
    inbound = floor._receive_panel
    for _ in range(2):
        inbound._sku_input.setText("BOX")
        inbound._qty_input.setValue(2)
        inbound._on_submit()
    assert stock_repository.quantity_at(WH1, "BOX") == 2


def test_console_locks_when_idle_or_when_the_account_is_switched_off(floor, qapp, monkeypatch):
    from depot_app.gui import console_window as cw

    floor._open_console(MANAGER)
    console = floor._console_window
    reasons = []
    console.locked.connect(reasons.append)
    monkeypatch.setattr(cw.account_repository, "is_session_valid", lambda s: True)
    assert console._check_session() is None
    later = console._activity.last + cw.IDLE_LOCK_SECONDS + 1
    assert "inactivity" in console._check_session(now=later)
    assert console.session is None and reasons
    floor._open_console(MANAGER)
    monkeypatch.setattr(cw.account_repository, "is_session_valid", lambda s: False)
    assert "Admin" in console._check_session()


def test_clicking_the_floor_login_button_does_not_pass_a_bool_as_the_session(floor, monkeypatch):
    """QPushButton.clicked sends `checked` (False). It once landed in _open_console's
    `session` parameter, which skipped the sign-in and opened a console that read
    "Not signed in" with dead buttons."""
    from PySide6.QtWidgets import QAbstractButton

    from depot_app.gui import main_window as mw
    from shared.i18n import tr

    window = floor
    seen = []
    monkeypatch.setattr(mw.MainWindow, "_open_console", lambda self, session=None: seen.append(session))
    from shared.textcase import upper

    wanted = upper(tr("depot.floor.admin_login"))
    button = next(b for b in window.findChildren(QAbstractButton) if b.text() == wanted)
    button.click()
    assert seen == [None]


def test_language_switch_saves_then_restarts_only_on_a_change(qapp):
    from shared import i18n
    from shared.gui_kit.language_switch import LanguageSwitch
    from depot_app.theme import INDUSTRY_PALETTE

    i18n.set_language("en")
    saved, restarted = [], []
    switch = LanguageSwitch(INDUSTRY_PALETTE, restart=lambda: restarted.append(1), save=saved.append)
    switch.choose("en")  # already English: nothing happens
    assert saved == [] and restarted == []
    switch.choose("tr")
    assert saved == ["tr"] and restarted == [1]


def test_floor_and_console_follow_the_database_without_a_refresh_button(floor, qapp):
    floor._watcher.stop()
    stock_repository.receive(WH1, "BOX", 5)
    floor._receive_panel.reload = lambda calls=[]: calls.append(1) or setattr(floor, "_seen_receive", len(calls))
    floor.refresh_live()
    assert floor._seen_receive == 1
    floor._open_console(MANAGER)
    console = floor._console_window
    console._watcher.stop()
    pump(qapp)
    before = console._movements_table.rowCount()
    stock_repository.receive(WH1, "BOX", 3)  # a change made elsewhere
    console.refresh_live()
    assert console._movements_table.rowCount() == before + 1


def test_floor_can_switch_to_another_depot_and_everything_follows(floor, qapp):
    assert floor._site_picker.count() == 2
    floor._open_console(MANAGER)
    first_console = floor._console_window
    assert floor.switch_warehouse("WH-02")
    pump(qapp)
    assert floor.warehouse.code == "WH-02"
    assert floor._receive_panel._location == WH2 and floor._dispatch_panel._location == WH2
    assert floor._low_stock_banner._location == WH2
    assert floor._site_picker.currentData() == "WH-02"
    assert floor._console_window is None and first_console.session is None  # the old depot's Console is closed
    assert "Gebze" in floor.windowTitle()
    assert floor.switch_warehouse("NOPE") is False and floor.warehouse.code == "WH-02"


def test_several_low_stock_alerts_slide_in_together_and_extra_ones_wait(floor, qapp):
    from database import transaction_repository
    from shared.models import LineItem, Transaction

    for n in range(6):
        product_repository.create(Product(f"P{n}", f"Item {n}", 1, 5, 4))
        stock_repository.transfer(UNASSIGNED, WH1, f"P{n}", 5)
    floor._alerts.start()
    for n in range(6):  # each sale takes the item to its reorder level
        transaction_repository.finalize_transaction(
            Transaction(items=[LineItem(f"P{n}", f"Item {n}", 1, 1)]), location=WH1)
    assert floor._alerts.poll() == 6
    pump(qapp)
    from depot_app.gui.slide_alerts import MAX_VISIBLE
    assert len(floor._alerts.active()) == MAX_VISIBLE and len(floor._alerts._waiting) == 6 - MAX_VISIBLE
    floor._alerts._dismiss(floor._alerts.active()[0])  # one goes, the next waiting one slides in
    assert len(floor._alerts.active()) == MAX_VISIBLE and len(floor._alerts._waiting) == 6 - MAX_VISIBLE - 1
