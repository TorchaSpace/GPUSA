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
