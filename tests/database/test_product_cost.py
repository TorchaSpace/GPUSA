"""Product cost: products.cost_price through product_repository, the
costs_for() hook the sale snapshot uses, and reading the snapshot back."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import database.connection as connection
from database import product_repository as products, sale_cost_repository, transaction_repository
from database.connection import connection_scope
from database.exceptions import ProductNotFoundError
from shared.models import LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def test_create_stores_and_returns_the_cost():
    products.create(Product("A", "Alpha", 10, 5, 0, cost_price=6.5))
    products.create(Product("B", "Beta", 10, 5, 0))
    assert products.get_by_barcode("A").cost_price == 6.5 and products.get_by_barcode("A").cost_known
    assert products.get_by_barcode("B").cost_price == 0.0 and not products.get_by_barcode("B").cost_known
    assert {p.barcode: p.cost_price for p in products.list_all()} == {"A": 6.5, "B": 0.0}


@pytest.mark.parametrize("typed, stored", [(1.005, 1.01), (2.004, 2.0), (0.125, 0.13), (0, 0.0), (7, 7.0)])
def test_cost_is_rounded_half_up_to_two_decimals(typed, stored):
    products.create(Product("A", "Alpha", 10, 0, 0, cost_price=typed))
    assert products.get_by_barcode("A").cost_price == stored
    assert products.set_cost("A", typed) == stored


@pytest.mark.parametrize("bad", [-0.01, -5, float("nan"), float("inf"), 2e9, "3", None, True])
def test_a_bad_cost_is_refused_everywhere(bad):
    with pytest.raises(ValueError):
        products.create(Product("A", "Alpha", 10, 0, 0, cost_price=bad))
    products.create(Product("A", "Alpha", 10, 0, 0, cost_price=2))
    with pytest.raises(ValueError):
        products.set_cost("A", bad)
    assert products.get_by_barcode("A").cost_price == 2.0


def test_update_never_touches_the_cost_but_set_cost_does():
    products.create(Product("A", "Alpha", 10, 0, 0, cost_price=4))
    edited = products.get_by_barcode("A")
    edited.name, edited.cost_price = "Alpha 2", 99.0
    products.update(edited)  # a form open for a minute must not write a stale cost back
    after = products.get_by_barcode("A")
    assert (after.name, after.cost_price) == ("Alpha 2", 4.0)

    assert products.set_cost("a", 5.25) == 5.25  # barcode in any letter case
    assert products.get_by_barcode("A").cost_price == 5.25
    with pytest.raises(ProductNotFoundError):
        products.set_cost("NOPE", 1)


def test_costs_for_reports_each_barcode_known_or_not():
    products.create(Product("A", "Alpha", 10, 0, 0, cost_price=6.5))
    products.create(Product("B", "Beta", 10, 0, 0))
    got = products.costs_for(["A", "B", "a", "ZZZ", "A"])
    assert got == {"A": (6.5, True), "B": (0.0, False), "a": (6.5, True), "ZZZ": (0.0, False)}
    assert products.costs_for([]) == {}


def test_costs_for_reads_inside_the_callers_transaction():
    products.create(Product("A", "Alpha", 10, 0, 0, cost_price=6.5))
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE products SET cost_price = 8 WHERE barcode = 'A'")
        assert products.costs_for(["A"], conn=conn) == {"A": (8.0, True)}  # sees its own uncommitted write
        conn.execute("ROLLBACK")
    assert products.costs_for(["A"]) == {"A": (6.5, True)}


# --- reading the snapshot back --------------------------------------------------


def _sell(items):
    return transaction_repository.finalize_transaction(Transaction(items=items))


def _snapshot(sale_id, rows):
    """What finalize_transaction will write once it calls costs_for(): (cost, known) per line, in order."""
    with connection_scope() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM transaction_items WHERE transaction_id = ? ORDER BY id", (sale_id,))]
        for item_id, (cost, known) in zip(ids, rows):
            conn.execute("UPDATE transaction_items SET unit_cost_at_sale = ?, cost_known = ? WHERE id = ?",
                         (cost, int(known), item_id))


def test_sales_made_before_costing_read_back_as_unknown_by_default():
    products.create(Product("A", "Alpha", 10, 50, 0))
    sale = _sell([LineItem("A", "Alpha", 10, 2)])
    assert [(i.unit_cost_at_sale, i.cost_known) for i in sale.items] == [(0.0, False)]
    with connection_scope() as conn:
        assert [tuple(r) for r in conn.execute("SELECT unit_cost_at_sale, cost_known FROM transaction_items")] == [(0.0, 0)]


def test_attach_costs_fills_the_lines_of_listed_sales():
    products.create(Product("A", "Alpha", 10, 50, 0, cost_price=6))
    products.create(Product("B", "Beta", 5, 50, 0))
    one = _sell([LineItem("A", "Alpha", 10, 2), LineItem("B", "Beta", 5, 1)])
    two = _sell([LineItem("B", "Beta", 5, 3), LineItem("A", "Alpha", 10, 1), LineItem("A", "Alpha", 10, 1)])
    _snapshot(one.id, [(6.0, True), (0.0, False)])
    _snapshot(two.id, [(0.0, False), (6.0, True), (5.5, True)])

    now = datetime.now()
    listed = transaction_repository.list_between(now - timedelta(days=1), now + timedelta(days=1))
    assert sale_cost_repository.attach_costs(listed) is listed
    by_id = {t.id: t for t in listed}
    assert [(i.product_barcode, i.unit_cost_at_sale, i.cost_known) for i in by_id[one.id].items] == [
        ("A", 6.0, True), ("B", 0.0, False)]
    assert [(i.product_barcode, i.unit_cost_at_sale, i.cost_known) for i in by_id[two.id].items] == [
        ("B", 0.0, False), ("A", 6.0, True), ("A", 5.5, True)]

    single = sale_cost_repository.attach_costs([transaction_repository.get_by_id(one.id)])[0]
    assert single.items[0].cost_known and single.items[0].unit_cost_at_sale == 6.0


def test_attach_costs_leaves_unsaved_sales_and_empty_lists_alone():
    unsaved = Transaction(items=[LineItem("A", "Alpha", 10, 1)])
    assert sale_cost_repository.attach_costs([unsaved]) == [unsaved] and not unsaved.items[0].cost_known
    assert sale_cost_repository.attach_costs([]) == []
