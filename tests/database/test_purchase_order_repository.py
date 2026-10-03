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
from shared.auth import Actor
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


ADMIN = Actor(badge_id="A-1", name="Ada Admin")


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
    product_repository.create(Product(barcode="NEW-1", name="Never used", price=1, stock_quantity=0))
    po_repo.set_price_range(PriceRange("NEW-1", 1, 2))
    product_repository.delete("NEW-1")

    assert [r.product_barcode for r in po_repo.list_price_ranges()] == [wrap]


def test_a_product_with_purchase_orders_cannot_be_deleted(wrap):
    from database.exceptions import ProductInUseError

    po_repo.submit(wrap, "Kuzey Ambalaj A.Ş.", 10, 600, "WH-01")
    with pytest.raises(ProductInUseError):
        product_repository.delete(wrap)


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

    approved = po_repo.approve(held.id, note="Supplier price rise confirmed", decided_by=ADMIN)

    assert approved.status == "sent"
    assert approved.was_approved is True
    assert approved.decided_at is not None
    assert approved.decision_note == "Supplier price rise confirmed"
    assert approved.decided_by == "Ada Admin · A-1"
    assert approved.hold_reason == "above_range"  # history kept
    assert po_repo.count_pending() == 0


def test_reject_a_held_order(wrap):
    held = _submit(wrap, 742.5)

    rejected = po_repo.reject(held.id, decided_by=ADMIN)

    assert rejected.status == "rejected"
    assert rejected.decided_at is not None
    assert po_repo.count_pending() == 0


def test_second_decision_is_refused(wrap):
    # Two admins on two machines: the first decision wins, the second is
    # told, rather than silently flipping an approval into a rejection.
    held = _submit(wrap, 742.5)
    po_repo.approve(held.id, decided_by=ADMIN)

    with pytest.raises(PurchaseOrderAlreadyDecidedError) as info:
        po_repo.reject(held.id, decided_by=ADMIN)

    assert info.value.status == "sent"
    assert po_repo.get(held.id).status == "sent"


def test_an_order_sent_directly_cannot_be_approved_or_rejected(wrap):
    sent = _submit(wrap, 600)

    with pytest.raises(PurchaseOrderAlreadyDecidedError):
        po_repo.reject(sent.id, decided_by=ADMIN)


def test_deciding_a_missing_order_raises():
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.approve(999, decided_by=ADMIN)
    with pytest.raises(PurchaseOrderNotFoundError):
        po_repo.get(999)


# --- actor required ---------------------------------------------------------


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_a_decision_without_an_actor_is_refused_and_changes_nothing(wrap, decide):
    held = _submit(wrap, 742.5)
    with pytest.raises(ValueError):
        getattr(po_repo, decide)(held.id)
    with pytest.raises(ValueError):
        getattr(po_repo, decide)(held.id, decided_by=None)
    after = po_repo.get(held.id)
    assert (after.status, after.decided_by, after.decided_at) == ("pending", None, None)


def test_decided_by_is_never_null_on_a_decided_order(wrap):
    held = _submit(wrap, 742.5)
    assert po_repo.reject(held.id, "no", decided_by=ADMIN).decided_by == "Ada Admin · A-1"


def test_pending_ids_track_which_orders_wait(wrap):
    a, b = _submit(wrap, 742.5), _submit(wrap, 800)
    assert po_repo.pending_ids() == [a.id, b.id]
    po_repo.approve(a.id, decided_by=ADMIN)
    c = _submit(wrap, 900)
    assert po_repo.pending_ids() == [b.id, c.id]  # same count as before, different set


# --- amount / quantity validation ---------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(price=float("nan")), dict(price=float("inf")), dict(price=-float("inf")), dict(price=0.004),
        dict(price=1e300), dict(price=1_000_000_001), dict(price="5"), dict(price=None),
        dict(qty=1_000_001), dict(qty=2.5), dict(qty="10"), dict(qty=True), dict(qty=None),
        dict(supplier="x" * 201), dict(site="   "),
    ],
)
def test_submit_rejects_bad_numbers_with_value_error(wrap, kwargs):
    fields = dict(qty=10, price=600, supplier="S", site="WH-01")
    fields.update(kwargs)
    with pytest.raises(ValueError):
        po_repo.submit(wrap, fields["supplier"], fields["qty"], fields["price"], fields["site"])
    assert po_repo.list_orders() == []


def test_unit_price_is_rounded_half_up_to_cents_and_judged_rounded(wrap):
    order = _submit(wrap, 680.004)  # rounds to 680.00 = top of the band, inclusive
    assert (order.unit_price, order.status) == (680.0, "sent")
    assert _submit(wrap, 600.005).unit_price == 600.01
    assert _submit(wrap, 1_000_000_000).unit_price == 1_000_000_000
    assert _submit(wrap, 600, qty=1_000_000).quantity == 1_000_000


@pytest.mark.parametrize(
    "low, high",
    [(float("nan"), 5), (1, float("nan")), (1, float("inf")), (float("-inf"), 5), (0, 1e300), (0, 2_000_000_000), (1, 0.004)],
)
def test_set_price_range_rejects_non_finite_and_huge(wrap, low, high):
    with pytest.raises(ValueError):
        po_repo.set_price_range(PriceRange("PLT-4410", low, high, None))
    assert po_repo.get_price_range("PLT-4410").max_unit_price == 680


def test_set_price_range_rounds_to_cents_and_allows_zero_minimum(wrap):
    po_repo.set_price_range(PriceRange("PLT-4410", 0, 10.005, None))
    stored = po_repo.get_price_range("PLT-4410")
    assert (stored.min_unit_price, stored.max_unit_price) == (0, 10.01)


def test_stray_sqlite_errors_become_data_access_errors(monkeypatch):
    import sqlite3

    from database.exceptions import DataAccessError

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(po_repo, "connection_scope", boom)
    for call in (lambda: po_repo.list_orders(), lambda: po_repo.count_pending(), lambda: po_repo.get(1),
                 lambda: po_repo.approve(1, decided_by=ADMIN), lambda: po_repo.submit("X", "S", 1, 5, "WH")):
        with pytest.raises(DataAccessError):
            call()


# --- site normalisation -------------------------------------------------------


def test_site_is_stored_upper_case_and_filtered_case_insensitively(wrap):
    a = _submit(wrap, 600, site=" wh-01 ")
    b = _submit(wrap, 600, site="WH-02")
    assert po_repo.get(a.id).site == "WH-01"
    assert [o.id for o in po_repo.list_orders(site="wh-01")] == [a.id]
    assert [o.id for o in po_repo.list_orders(site="WH-02", limit=1)] == [b.id]
    assert po_repo.list_orders(site="nowhere") == []


def test_legacy_mixed_case_site_rows_still_match(wrap):
    a = _submit(wrap, 600, site="WH-01")
    with connection.connection_scope() as conn:
        conn.execute("UPDATE purchase_orders SET site = 'Wh-01' WHERE id = ?", (a.id,))
    assert [o.id for o in po_repo.list_orders(site="WH-01")] == [a.id]


def test_order_total_rounds_half_up_via_decimal():
    from shared.models import PurchaseOrder

    def total(price, qty):
        return PurchaseOrder("B", "n", "s", qty, price, "WH", "pending").total

    assert total(0.125, 1) == 0.13
    assert total(1.005, 3) == 3.02  # float math gives 3.0149999... -> 3.01
    assert total(600, 40) == 24000
    assert total(19.99, 3) == 59.97
