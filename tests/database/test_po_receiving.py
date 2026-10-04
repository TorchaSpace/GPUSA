"""Receiving goods against purchase orders and cancelling orders:
database.purchase_order_repository.receive_against_order / cancel_order,
and the weighted-average product cost they keep."""

from __future__ import annotations

import threading

import pytest

import database.connection as connection
from database import product_repository, purchase_order_repository as po_repo, stock_repository
from database.connection import connection_scope
from database.exceptions import (
    CapacityExceededError,
    LocationInactiveError,
    PurchaseOrderCancelNotAllowedError,
    PurchaseOrderNotFoundError,
    PurchaseOrderOverReceiveError,
    PurchaseOrderStateError,
    UnknownLocationError,
)
from shared.models import PriceRange, Product, StockLocation, Warehouse
from tests.po_support import (
    ADMIN, CASHIER, DEPOT, OTHER_DEPOT, STRANGER, WH1,
    pending_order, seed_people, seed_world, sent_order,
)
from tests.stock_invariant import assert_totals_consistent


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def world():
    seed_world()
    seed_people()


def _cost(barcode="PLT-4410"):
    return product_repository.get_by_barcode(barcode).cost_price


def _on_hand():
    return stock_repository.quantity_at(WH1, "PLT-4410")


def _total():
    return product_repository.get_by_barcode("PLT-4410").stock_quantity


def _movements():
    return [m for m in stock_repository.list_movements(limit=None) if m["location"] == WH1]


# --- receiving: the happy paths ------------------------------------------------


def test_receiving_in_full_books_stock_movement_and_status(world):
    order = sent_order(100, 600)
    done = po_repo.receive_against_order(order.id, 100, ADMIN, WH1)

    assert (done.status, done.received_qty, done.remaining_qty) == ("received", 100, 0)
    assert done.received_by == "Ada Admin · A-1" and done.received_at
    assert _on_hand() == 100 and _total() == 100
    (movement,) = _movements()
    assert (movement["movement_type"], movement["quantity"], movement["reason"]) == ("receive", 100, "receive")
    assert movement["reference"] == order.number and movement["handled_by"] == "Ada Admin · A-1"
    assert order.number in movement["note"]
    assert_totals_consistent()
    assert po_repo.get(order.id) == done


def test_partial_deliveries_add_up(world):
    order = sent_order(100, 600)
    first = po_repo.receive_against_order(order.id, 30, DEPOT, WH1)
    assert (first.status, first.received_qty, first.remaining_qty, first.can_receive) == ("partially_received", 30, 70, True)
    second = po_repo.receive_against_order(order.id, 70, DEPOT, WH1)
    assert (second.status, second.received_qty, second.can_receive) == ("received", 100, False)
    assert _on_hand() == 100 and len(_movements()) == 2
    assert_totals_consistent()


def test_an_approved_order_can_be_received(world):
    held = pending_order(10, 742.5)
    approved = po_repo.approve(held.id, decided_by=ADMIN)
    assert approved.status == "sent" and approved.was_approved
    done = po_repo.receive_against_order(held.id, 10, DEPOT, WH1)
    assert done.status == "received" and done.was_approved  # still reads as approved


def test_list_orders_filters_by_the_new_statuses(world):
    a, b, c = sent_order(10), sent_order(10), sent_order(10)
    po_repo.receive_against_order(a.id, 10, DEPOT, WH1)
    po_repo.receive_against_order(b.id, 4, DEPOT, WH1)
    po_repo.cancel_order(c.id, ADMIN)
    assert [o.id for o in po_repo.list_orders(status="received")] == [a.id]
    assert [o.id for o in po_repo.list_orders(status="partially_received")] == [b.id]
    assert [o.id for o in po_repo.list_orders(status="cancelled")] == [c.id]


# --- receiving: refusals ---------------------------------------------------------


def test_over_receiving_is_refused_with_a_clear_message_and_changes_nothing(world):
    order = sent_order(100, 600)
    with pytest.raises(PurchaseOrderOverReceiveError) as caught:
        po_repo.receive_against_order(order.id, 101, DEPOT, WH1)
    assert (caught.value.requested, caught.value.remaining) == (101, 100)
    assert "only 100 units still due" in str(caught.value)
    assert po_repo.get(order.id).received_qty == 0 and _on_hand() == 0 and _movements() == []

    po_repo.receive_against_order(order.id, 60, DEPOT, WH1)
    with pytest.raises(PurchaseOrderOverReceiveError) as again:
        po_repo.receive_against_order(order.id, 41, DEPOT, WH1)
    assert again.value.remaining == 40
    assert po_repo.get(order.id).received_qty == 60 and _on_hand() == 60


@pytest.mark.parametrize("make", ["pending", "rejected", "cancelled", "received"])
def test_receiving_against_any_other_state_is_refused(world, make):
    order = pending_order(10) if make == "pending" else sent_order(10)
    if make == "rejected":
        order = pending_order(10)
        po_repo.reject(order.id, "no", decided_by=ADMIN)
    elif make == "cancelled":
        po_repo.cancel_order(order.id, ADMIN)
    elif make == "received":
        po_repo.receive_against_order(order.id, 10, DEPOT, WH1)
    before = _on_hand()
    with pytest.raises(PurchaseOrderStateError) as caught:
        po_repo.receive_against_order(order.id, 1, DEPOT, WH1)
    assert caught.value.status == make and caught.value.action == "received"
    assert _on_hand() == before
    assert po_repo.get(order.id).status == make


@pytest.mark.parametrize("quantity", [0, -5, 2.5, "5", None, True])
def test_quantity_must_be_a_whole_number_above_zero(world, quantity):
    order = sent_order(10)
    with pytest.raises(ValueError):
        po_repo.receive_against_order(order.id, quantity, DEPOT, WH1)
    assert po_repo.get(order.id).received_qty == 0 and _on_hand() == 0


def test_an_actor_and_a_warehouse_are_required(world):
    order = sent_order(10)
    with pytest.raises(ValueError):
        po_repo.receive_against_order(order.id, 5, None, WH1)
    for bad in (StockLocation.dealership("001"), stock_repository.UNASSIGNED, "WH-01", None):
        with pytest.raises(ValueError):
            po_repo.receive_against_order(order.id, 5, DEPOT, bad)
    assert po_repo.get(order.id).received_qty == 0


def test_unknown_order_is_not_found(world):
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.receive_against_order(999, 1, DEPOT, WH1)
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.cancel_order(999, ADMIN)


def test_an_unknown_warehouse_is_refused_and_rolls_back(world):
    order = sent_order(10)
    with pytest.raises(UnknownLocationError):
        po_repo.receive_against_order(order.id, 5, DEPOT, StockLocation.warehouse("WH-99"))
    assert po_repo.get(order.id).received_qty == 0 and po_repo.get(order.id).status == "sent"


def test_an_inactive_warehouse_is_refused_and_rolls_back(world):
    from database import warehouse_repository

    warehouse = warehouse_repository.get_by_code("WH-01")
    warehouse.is_active = False
    warehouse_repository.update(warehouse)
    order = sent_order(10)
    with pytest.raises(LocationInactiveError):
        po_repo.receive_against_order(order.id, 5, DEPOT, WH1)
    assert po_repo.get(order.id).received_qty == 0 and _on_hand() == 0


def test_a_full_warehouse_is_refused_and_rolls_back_the_order(world):
    from database import warehouse_repository

    warehouse_repository.update(Warehouse(code="WH-01", name="İstanbul Merkez", city="Tuzla", capacity_units=50))
    order = sent_order(100)
    po_repo.receive_against_order(order.id, 40, DEPOT, WH1)
    with pytest.raises(CapacityExceededError):
        po_repo.receive_against_order(order.id, 20, DEPOT, WH1)  # 40 + 20 > 50
    after = po_repo.get(order.id)
    assert (after.status, after.received_qty) == ("partially_received", 40) and _on_hand() == 40
    assert _cost() == 600.0  # the failed receipt did not touch the cost
    po_repo.receive_against_order(order.id, 10, DEPOT, WH1)  # exactly full is fine
    assert _on_hand() == 50


def test_an_inactive_product_cannot_be_received(world):
    order = sent_order(10)
    product_repository.set_active("PLT-4410", False)
    from database.exceptions import ProductInactiveError

    with pytest.raises(ProductInactiveError):
        po_repo.receive_against_order(order.id, 5, DEPOT, WH1)
    assert po_repo.get(order.id).received_qty == 0


# --- atomicity -------------------------------------------------------------------


def test_a_failed_movement_write_rolls_back_order_stock_and_cost(world, monkeypatch):
    order = sent_order(100, 600)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(stock_repository, "log_movement", boom)
    with pytest.raises(RuntimeError):
        po_repo.receive_against_order(order.id, 40, DEPOT, WH1)

    after = po_repo.get(order.id)
    assert (after.status, after.received_qty, after.received_at, after.received_by) == ("sent", 0, None, None)
    assert _on_hand() == 0 and _total() == 0 and _cost() == 0.0


def test_a_failed_stock_level_write_rolls_back_the_order(world, monkeypatch):
    order = sent_order(100, 600)
    real = stock_repository.change_level

    def boom(*args, **kwargs):
        real(*args, **kwargs)  # the level write happened ...
        raise RuntimeError("crash after the stock write")  # ... then the transaction fails

    monkeypatch.setattr(stock_repository, "change_level", boom)
    with pytest.raises(RuntimeError):
        po_repo.receive_against_order(order.id, 40, DEPOT, WH1)

    assert po_repo.get(order.id).received_qty == 0 and po_repo.get(order.id).status == "sent"
    with connection_scope() as conn:  # nothing half-written, in a fresh connection
        assert conn.execute("SELECT COUNT(*) FROM stock_levels WHERE quantity > 0").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 0
    assert_totals_consistent()


def test_a_failed_cost_update_rolls_back_everything(world, monkeypatch):
    order = sent_order(100, 600)
    monkeypatch.setattr(po_repo, "weighted_average_cost", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        po_repo.receive_against_order(order.id, 40, DEPOT, WH1)
    assert po_repo.get(order.id).received_qty == 0 and _on_hand() == 0


# --- cost ------------------------------------------------------------------------


def test_first_receipt_with_nothing_on_hand_sets_the_cost_to_the_unit_price(world):
    order = sent_order(100, 612.34)
    assert _cost() == 0.0
    po_repo.receive_against_order(order.id, 10, DEPOT, WH1)
    assert _cost() == 612.34


def test_cost_is_the_weighted_average_over_the_whole_network(world):
    # 100 units already exist (unassigned), costing 10.00 each.
    product_repository.create(Product("W", "Widget", 30, 100, 0, cost_price=10.0))
    repo = po_repo
    repo.set_price_range(PriceRange("W", 1, 100))
    order = repo.submit("W", "Sup", 50, 16.0, "WH-01 · İstanbul Merkez", raised_by=DEPOT)
    repo.receive_against_order(order.id, 50, DEPOT, WH1)  # (100*10 + 50*16) / 150 = 12.00
    assert product_repository.get_by_barcode("W").cost_price == 12.0
    # now 150 on hand at 12.00; another 30 at 20.00 -> (1800 + 600) / 180 = 13.333... -> 13.33
    second = repo.submit("W", "Sup", 30, 20.0, "WH-01 · İstanbul Merkez", raised_by=DEPOT)
    repo.receive_against_order(second.id, 30, DEPOT, WH1)
    assert product_repository.get_by_barcode("W").cost_price == 13.33


def test_partial_receipts_each_move_the_average(world):
    product_repository.set_cost("PLT-4410", 500)
    order = sent_order(100, 600)
    # nothing on hand: the first receipt takes the price (the old cost is irrelevant with 0 units)
    po_repo.receive_against_order(order.id, 20, DEPOT, WH1)
    assert _cost() == 600.0
    other = po_repo.submit("PLT-4410", "S", 20, 650.0, "WH-01 · İstanbul Merkez", raised_by=DEPOT)
    assert other.status == "sent"  # 650 is inside the 520-680 band
    po_repo.receive_against_order(other.id, 20, DEPOT, WH1)  # (20*600 + 20*650) / 40 = 625
    assert _cost() == 625.0


def test_an_unknown_cost_is_not_averaged_in_as_free(world):
    product_repository.create(Product("U", "Unknown cost", 30, 90, 0))  # 90 on hand, cost 0 = unknown
    po_repo.set_price_range(PriceRange("U", 1, 100))
    order = po_repo.submit("U", "Sup", 10, 20.0, "WH-01 · İstanbul Merkez", raised_by=DEPOT)
    po_repo.receive_against_order(order.id, 10, DEPOT, WH1)
    assert product_repository.get_by_barcode("U").cost_price == 20.0  # not (90*0 + 10*20)/100 = 2.00


def test_cost_rounds_half_up_to_cents(world):
    product_repository.create(Product("H", "Half", 1, 1, 0, cost_price=0.01))
    po_repo.set_price_range(PriceRange("H", 0.01, 1))
    order = po_repo.submit("H", "Sup", 1, 0.02, "WH-01 · İstanbul Merkez", raised_by=DEPOT)
    po_repo.receive_against_order(order.id, 1, DEPOT, WH1)  # (0.01 + 0.02) / 2 = 0.015 -> 0.02
    assert product_repository.get_by_barcode("H").cost_price == 0.02


# --- cancelling ------------------------------------------------------------------


def test_admin_can_cancel_pending_sent_and_partially_received_orders(world):
    pending, sent, partial = pending_order(10), sent_order(10), sent_order(10)
    po_repo.receive_against_order(partial.id, 4, DEPOT, WH1)

    for order in (pending, sent, partial):
        done = po_repo.cancel_order(order.id, ADMIN)
        assert done.status == "cancelled" and done.cancelled_by == "Ada Admin · A-1" and done.cancelled_at
    assert po_repo.get(partial.id).received_qty == 4 and _on_hand() == 4  # received stock is kept
    assert po_repo.count_pending() == 0
    with pytest.raises(PurchaseOrderStateError):  # no further receiving
        po_repo.receive_against_order(partial.id, 1, DEPOT, WH1)
    assert _on_hand() == 4


@pytest.mark.parametrize("final", ["received", "rejected", "cancelled"])
def test_finished_orders_cannot_be_cancelled(world, final):
    order = pending_order(10) if final == "rejected" else sent_order(10)
    if final == "received":
        po_repo.receive_against_order(order.id, 10, DEPOT, WH1)
    elif final == "rejected":
        po_repo.reject(order.id, "no", decided_by=ADMIN)
    else:
        po_repo.cancel_order(order.id, ADMIN)
    with pytest.raises(PurchaseOrderStateError) as caught:
        po_repo.cancel_order(order.id, ADMIN)
    assert caught.value.status == final and caught.value.action == "cancelled"
    assert po_repo.get(order.id).status == final


def test_a_depot_manager_cancels_only_their_own_pending_orders(world):
    mine = pending_order(10, raised_by=DEPOT)
    theirs = pending_order(10, raised_by=OTHER_DEPOT)
    approved_mine = pending_order(10, raised_by=DEPOT)
    po_repo.approve(approved_mine.id, decided_by=ADMIN)
    direct = sent_order(10, raised_by=DEPOT)

    assert po_repo.cancel_order(mine.id, DEPOT).status == "cancelled"
    with pytest.raises(PurchaseOrderCancelNotAllowedError) as not_yours:
        po_repo.cancel_order(theirs.id, DEPOT)
    assert not_yours.value.reason == "not_yours"
    for order in (approved_mine, direct):
        with pytest.raises(PurchaseOrderCancelNotAllowedError) as admin_only:
            po_repo.cancel_order(order.id, DEPOT)
        assert admin_only.value.reason == "admin_only" and "administrator" in str(admin_only.value)
    assert po_repo.get(theirs.id).status == "pending" and po_repo.get(direct.id).status == "sent"


def test_cashiers_unknown_badges_and_disabled_accounts_cannot_cancel(world):
    order = pending_order(10, raised_by=CASHIER)
    for actor in (CASHIER, STRANGER):
        with pytest.raises(PurchaseOrderCancelNotAllowedError) as caught:
            po_repo.cancel_order(order.id, actor)
        assert caught.value.reason == "role"
    with connection_scope() as conn:
        conn.execute("UPDATE accounts SET is_active = 0 WHERE role = 'depot_manager'")
    with pytest.raises(PurchaseOrderCancelNotAllowedError):
        po_repo.cancel_order(pending_order(10).id, DEPOT)
    with pytest.raises(ValueError):
        po_repo.cancel_order(order.id, None)
    assert po_repo.get(order.id).status == "pending"


def test_the_depot_managers_badge_match_ignores_letter_case(world):
    mine = pending_order(10, raised_by=DEPOT)
    from shared.auth import Actor

    assert po_repo.cancel_order(mine.id, Actor("d-1", "Deniz Depo")).status == "cancelled"


# --- concurrency -----------------------------------------------------------------


def _run_in_threads(jobs):
    results = [None] * len(jobs)
    barrier = threading.Barrier(len(jobs))

    def runner(index, job):
        barrier.wait()
        try:
            results[index] = ("ok", job())
        except Exception as exc:  # noqa: BLE001 - the test inspects the type
            results[index] = ("err", exc)

    threads = [threading.Thread(target=runner, args=(i, job)) for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_machines_receiving_the_same_order_cannot_over_receive(world):
    order = sent_order(100)
    results = _run_in_threads([lambda: po_repo.receive_against_order(order.id, 60, DEPOT, WH1)] * 2)
    kinds = sorted(r[0] for r in results)
    assert kinds == ["err", "ok"]
    assert isinstance([r[1] for r in results if r[0] == "err"][0], PurchaseOrderOverReceiveError)
    assert po_repo.get(order.id).received_qty == 60 and _on_hand() == 60
    assert_totals_consistent()


def test_many_concurrent_deliveries_add_up_exactly(world):
    order = sent_order(100)
    results = _run_in_threads([lambda: po_repo.receive_against_order(order.id, 10, DEPOT, WH1)] * 10)
    assert [r[0] for r in results] == ["ok"] * 10
    final = po_repo.get(order.id)
    assert (final.status, final.received_qty) == ("received", 100) and _on_hand() == 100
    assert len(_movements()) == 10
    assert_totals_consistent()


def test_a_cancel_racing_a_full_receipt_has_one_consistent_winner(world):
    order = sent_order(100)
    results = _run_in_threads([
        lambda: po_repo.receive_against_order(order.id, 100, DEPOT, WH1),
        lambda: po_repo.cancel_order(order.id, ADMIN),
    ])
    final = po_repo.get(order.id)
    assert final.status in ("received", "cancelled")
    if final.status == "received":
        assert results[0][0] == "ok" and results[1][0] == "err" and _on_hand() == 100
    else:
        assert results[1][0] == "ok" and results[0][0] == "err" and _on_hand() == 0
    assert_totals_consistent()


def test_two_cancels_race_one_wins(world):
    order = sent_order(10)
    results = _run_in_threads([lambda: po_repo.cancel_order(order.id, ADMIN)] * 2)
    assert sorted(r[0] for r in results) == ["err", "ok"]
    assert isinstance([r[1] for r in results if r[0] == "err"][0], PurchaseOrderStateError)


# --- public stock API is unchanged ------------------------------------------------


def test_plain_stock_receive_still_works_and_has_no_reference(world):
    assert stock_repository.receive(WH1, "PLT-4410", 7, note="n") == 7
    (movement,) = _movements()
    assert movement["reference"] is None and movement["reason"] == "receive" and movement["note"] == "n"
    assert_totals_consistent()
