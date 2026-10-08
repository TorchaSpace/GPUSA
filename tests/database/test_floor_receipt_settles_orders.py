"""Receiving at the Floor settles the depot's open purchase orders for that product,
so the same goods are never booked twice (once at the Floor, once from the order)."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import inventory_repository, product_repository, purchase_order_repository as po_repo, stock_repository
from database.exceptions import PurchaseOrderStateError
from tests.po_support import ADMIN, DEPOT, WH1, seed_people, seed_world, sent_order
from tests.stock_invariant import assert_totals_consistent


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    seed_world()
    seed_people()


def _order(order_id):
    return next(o for o in po_repo.list_orders() if o.id == order_id)


def test_a_floor_receipt_marks_the_waiting_order_received_and_it_cannot_be_received_again():
    order = sent_order(quantity=100)
    inventory_repository.receive_stock("PLT-4410", 100, location=WH1, actor=DEPOT)
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 100
    assert product_repository.get_by_barcode("PLT-4410").stock_quantity == 100
    done = _order(order.id)
    assert done.status == "received" and done.received_qty == 100
    with pytest.raises(PurchaseOrderStateError):
        po_repo.receive_against_order(order.id, 100, DEPOT, WH1)  # nothing left to receive: no double count
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 100
    assert_totals_consistent()


def test_a_partial_floor_receipt_leaves_the_rest_due_and_extra_units_are_plain_receipts():
    order = sent_order(quantity=100)
    inventory_repository.receive_stock("PLT-4410", 30, location=WH1)  # no operator: still settles
    assert _order(order.id).status == "partially_received" and _order(order.id).received_qty == 30
    inventory_repository.receive_stock("PLT-4410", 90, location=WH1, actor=DEPOT)  # 70 due, 20 beyond
    assert _order(order.id).status == "received"
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 120
    assert_totals_consistent()


def test_a_receipt_with_no_order_waiting_is_an_ordinary_receipt():
    inventory_repository.receive_stock("PLT-4410", 5, location=WH1, actor=DEPOT)
    assert stock_repository.quantity_at(WH1, "PLT-4410") == 5
    assert_totals_consistent()


def test_another_depots_order_is_left_alone():
    from database import warehouse_repository
    from shared.models import StockLocation, Warehouse
    warehouse_repository.create(Warehouse(code="WH-02", name="Gebze"))
    order = sent_order(quantity=50)  # raised for WH-01
    inventory_repository.receive_stock("PLT-4410", 50, location=StockLocation.warehouse("WH-02"), actor=DEPOT)
    assert _order(order.id).status == "sent" and _order(order.id).received_qty == 0
