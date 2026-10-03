"""Regression tests for database.purchase_order_repository - safe price
bands, submit()'s send-vs-hold decision, and approve()/reject()."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import product_repository, purchase_order_repository as po_repo
from database.exceptions import (
    ProductNotFoundError,
    PurchaseOrderAlreadyDecidedError,
    PurchaseOrderNotFoundError,
)
from shared.models import PriceRange, Product, hold_reason_for


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def wrap():
    product_repository.create(Product(barcode="PLT-4410", name="Pallet wrap 500mm", price=900, stock_quantity=5))
    po_repo.set_price_range(PriceRange("PLT-4410", 520, 680, "Kuzey Ambalaj A.Ş."))
    return "PLT-4410"


def _submit(barcode, price, qty=40, supplier="Kuzey Ambalaj A.Ş.", site="WH-01"):
    return po_repo.submit(barcode, supplier, qty, price, site)


# --- hold_reason_for (shared rule) ------------------------------------------


@pytest.mark.parametrize(
    "price, expected",
    [(520, None), (600, None), (680, None), (680.01, "above_range"), (519.99, "below_range")],
)
def test_hold_reason_band_is_inclusive(price, expected):
    assert hold_reason_for(PriceRange("X", 520, 680), price) == expected


def test_hold_reason_without_a_band_is_no_range():
    assert hold_reason_for(None, 10) == "no_range"


# --- price ranges --------------------------------------------------------


def test_set_and_get_price_range(wrap):
    band = po_repo.get_price_range(wrap)
    assert (band.min_unit_price, band.max_unit_price, band.default_supplier) == (520, 680, "Kuzey Ambalaj A.Ş.")


def test_set_price_range_replaces_existing(wrap):
    po_repo.set_price_range(PriceRange(wrap, 500, 700, None))

    band = po_repo.get_price_range(wrap)
    assert (band.min_unit_price, band.max_unit_price, band.default_supplier) == (500, 700, None)
    assert len(po_repo.list_price_ranges()) == 1


def test_price_range_for_unknown_product_raises():
    with pytest.raises(ProductNotFoundError):
        po_repo.set_price_range(PriceRange("NOPE", 1, 2))


@pytest.mark.parametrize("low, high", [(10, 5), (-1, 5)])
def test_invalid_price_range_raises(wrap, low, high):
    with pytest.raises(ValueError):
        po_repo.set_price_range(PriceRange(wrap, low, high))


def test_no_band_returns_none_and_delete_is_idempotent(wrap):
    po_repo.delete_price_range(wrap)
    po_repo.delete_price_range(wrap)

    assert po_repo.get_price_range(wrap) is None


def test_deleting_the_product_removes_its_band(wrap):
    product_repository.delete(wrap)

    assert po_repo.list_price_ranges() == []


# --- submit ----------------------------------------------------------------


def test_in_range_order_is_sent_directly(wrap):
    order = _submit(wrap, 600)

    assert order.status == "sent"
    assert order.hold_reason is None
    assert order.decided_at is None
    assert order.was_approved is False
    assert order.number == f"PO-{order.id:05d}"
    assert order.product_name == "Pallet wrap 500mm"
    assert order.total == 24000
    assert (order.range_min, order.range_max) == (520, 680)


def test_above_range_order_is_held(wrap):
    order = _submit(wrap, 742.5)

    assert (order.status, order.hold_reason) == ("pending", "above_range")
    assert po_repo.count_pending() == 1


def test_below_range_order_is_held(wrap):
    assert _submit(wrap, 50).hold_reason == "below_range"


def test_order_for_product_without_a_band_is_held(wrap):
    po_repo.delete_price_range(wrap)

    order = _submit(wrap, 600)

    assert (order.status, order.hold_reason) == ("pending", "no_range")
    assert (order.range_min, order.range_max) == (None, None)


def test_order_keeps_the_band_it_was_judged_against(wrap):
    order = _submit(wrap, 600)
    po_repo.set_price_range(PriceRange(wrap, 1, 2))

    assert (po_repo.get(order.id).range_min, po_repo.get(order.id).range_max) == (520, 680)


def test_order_keeps_the_product_name_at_order_time(wrap):
    order = _submit(wrap, 600)
    product = product_repository.get_by_barcode(wrap)
    product.name = "Renamed"
    product_repository.update(product)

    assert po_repo.get(order.id).product_name == "Pallet wrap 500mm"


@pytest.mark.parametrize(
    "kwargs",
    [dict(qty=0), dict(qty=-3), dict(price=0), dict(supplier="  "), dict(site="")],
)
def test_submit_validates_inputs(wrap, kwargs):
    fields = dict(qty=10, price=600, supplier="S", site="WH-01")
    fields.update(kwargs)
    with pytest.raises(ValueError):
        po_repo.submit(wrap, fields["supplier"], fields["qty"], fields["price"], fields["site"])
    assert po_repo.list_orders() == []


def test_submit_unknown_product_raises():
    with pytest.raises(ProductNotFoundError):
        _submit("NOPE", 10)
    assert po_repo.list_orders() == []


def test_list_orders_newest_first_and_filters(wrap):
    first = _submit(wrap, 600, site="WH-01")
    second = _submit(wrap, 900, site="WH-02")
    third = _submit(wrap, 610, site="WH-01")

    assert [o.id for o in po_repo.list_orders()] == [third.id, second.id, first.id]
    assert [o.id for o in po_repo.list_orders(site="WH-01")] == [third.id, first.id]
    assert [o.id for o in po_repo.list_pending()] == [second.id]
    assert [o.id for o in po_repo.list_orders(limit=1)] == [third.id]


# --- approve / reject ------------------------------------------------------


def test_approve_sends_a_held_order(wrap):
    held = _submit(wrap, 742.5)

    approved = po_repo.approve(held.id, note="Supplier price rise confirmed")

    assert approved.status == "sent"
    assert approved.was_approved is True
    assert approved.decided_at is not None
    assert approved.decision_note == "Supplier price rise confirmed"
    assert approved.hold_reason == "above_range"  # history kept
    assert po_repo.count_pending() == 0


def test_reject_a_held_order(wrap):
    held = _submit(wrap, 742.5)

    rejected = po_repo.reject(held.id)

    assert rejected.status == "rejected"
    assert rejected.decided_at is not None
    assert po_repo.count_pending() == 0


def test_second_decision_is_refused(wrap):
    # Two admins on two machines: the first decision wins, the second is
    # told, rather than silently flipping an approval into a rejection.
    held = _submit(wrap, 742.5)
    po_repo.approve(held.id)

    with pytest.raises(PurchaseOrderAlreadyDecidedError) as info:
        po_repo.reject(held.id)

    assert info.value.status == "sent"
    assert po_repo.get(held.id).status == "sent"


def test_an_order_sent_directly_cannot_be_approved_or_rejected(wrap):
    sent = _submit(wrap, 600)

    with pytest.raises(PurchaseOrderAlreadyDecidedError):
        po_repo.reject(sent.id)


def test_deciding_a_missing_order_raises():
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.approve(999)
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.get(999)
