"""Distribute stock: one product split across several places, all or nothing."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import dealership_repository, product_repository, stock_repository, warehouse_repository
from shared.models import UNASSIGNED, Dealership, Product, StockLocation, Warehouse
from tests.gui_support import pump, qapp  # noqa: F401

WH1, WH2 = StockLocation.warehouse("WH-01"), StockLocation.warehouse("WH-02")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    warehouse_repository.create(Warehouse(code="WH-01", name="Istanbul"))
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze", capacity_units=20))
    product_repository.create(Product("BOX", "Carton", 5, 0, 1))
    stock_repository.receive(UNASSIGNED, "BOX", 100)


def test_units_are_split_across_places_and_the_total_is_unchanged():
    placed = stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 60), (WH2, 15)])
    assert placed == 75
    assert stock_repository.quantity_at(WH1, "BOX") == 60
    assert stock_repository.quantity_at(WH2, "BOX") == 15
    assert stock_repository.quantity_at(UNASSIGNED, "BOX") == 25
    assert product_repository.get_by_barcode("BOX").stock_quantity == 100


def test_nothing_moves_if_one_leg_fails():
    with pytest.raises(Exception):
        stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 30), (WH2, 25)])  # Gebze holds 20
    assert stock_repository.quantity_at(WH1, "BOX") == 0 and stock_repository.quantity_at(UNASSIGNED, "BOX") == 100
    with pytest.raises(Exception):
        stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 60), (StockLocation.warehouse("WH-01"), 5)])
    with pytest.raises(Exception):
        stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 70), (WH2, 20), ])  # 90 fits...
        stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 20)])  # ...but only 10 left
    assert stock_repository.quantity_at(UNASSIGNED, "BOX") == 10


def test_an_empty_plan_is_refused():
    with pytest.raises(Exception):
        stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 0)])


def test_the_popup_checks_the_sum_and_applies(qapp):
    from admin_app.gui.components.stock_distribute_popup import StockDistributePopup

    popup = StockDistributePopup()
    popup.set_choices(product_repository.list_all(), warehouse_repository.list_all(), dealership_repository.list_all())
    assert len(popup._spins) == 2 and not popup._apply_button.isEnabled()
    popup._spins[0].setValue(70)
    popup._spins[1].setValue(40)
    assert not popup._apply_button.isEnabled() and "10" in popup._left.text()  # 10 too many
    popup._spins[1].setValue(20)
    assert popup._apply_button.isEnabled()
    seen = []
    popup.stock_changed.connect(lambda: seen.append(1))
    popup._save()
    assert stock_repository.quantity_at(WH1, "BOX") == 70 and stock_repository.quantity_at(WH2, "BOX") == 20
    assert seen == [1] and all(s.value() == 0 for s in popup._spins)  # ready for the next one
    popup.close()


def test_the_unplaced_warning_names_what_is_left_and_goes_away_when_all_is_placed(qapp):
    from admin_app.gui.pages.warehouses_page import WarehousesPage

    product_repository.create(Product("TAPE", "Tape roll", 2, 4, 1))
    page = WarehousesPage()
    page.show()
    pump(qapp)
    assert page._banner.isVisible() and "Carton (100)" in page._banner_text.text() and "Tape roll (4)" in page._banner_text.text()
    page._open_distribute()
    assert page._distribute_popup._barcode() == "BOX"  # opens on the product with the most waiting
    stock_repository.distribute(UNASSIGNED, "BOX", [(WH1, 100)])
    stock_repository.distribute(UNASSIGNED, "TAPE", [(WH1, 4)])
    page.reload()
    assert not page._banner.isVisible()
    page.close()


def test_counting_a_shelf_places_the_waiting_units_instead_of_adding_them_twice():
    stock_repository.set_count(WH1, "BOX", 100)  # the 100 sitting 'unplaced' are on this shelf
    assert stock_repository.quantity_at(WH1, "BOX") == 100
    assert stock_repository.quantity_at(UNASSIGNED, "BOX") == 0
    assert product_repository.get_by_barcode("BOX").stock_quantity == 100
    stock_repository.set_count(WH1, "BOX", 130)  # 30 more really were found
    assert product_repository.get_by_barcode("BOX").stock_quantity == 130
    stock_repository.set_count(WH2, "BOX", 5)
    assert product_repository.get_by_barcode("BOX").stock_quantity == 135


def test_clearing_the_unplaced_pile_removes_the_double_count(qapp):
    from admin_app.gui.pages.warehouses_page import WarehousesPage

    # 100 units were counted into a warehouse by hand BEFORE counts absorbed unplaced stock: the old double count
    with connection.connection_scope() as conn:
        stock_repository.change_level(conn, WH1, "BOX", 100, change_total=True)
    assert product_repository.get_by_barcode("BOX").stock_quantity == 200
    page = WarehousesPage()
    page.show()
    pump(qapp)
    assert page.clear_unplaced() == 100
    assert stock_repository.quantity_at(UNASSIGNED, "BOX") == 0 and stock_repository.quantity_at(WH1, "BOX") == 100
    assert product_repository.get_by_barcode("BOX").stock_quantity == 100
    assert not page._banner.isVisible()
    page.close()
