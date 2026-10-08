"""The live feed: actions write their own event in the same transaction; quiet by default."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import activity_repository, product_repository, stock_repository, transaction_repository
from shared.models import UNASSIGNED, LineItem, Product, Transaction


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _kinds(after: int = 0) -> list[str]:
    return [e.kind for e in activity_repository.list_since(after)]


def test_a_sale_leaves_one_event_with_its_numbers():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 50, 2))
    sale = transaction_repository.finalize_transaction(Transaction(items=[LineItem("SKU-1", "Widget", 5.00, 3)]))
    events = [e for e in activity_repository.list_since(0) if e.kind == "sale"]
    assert len(events) == 1
    assert events[0].data["id"] == sale.id and events[0].data["units"] == 3 and events[0].source == "pos"


def test_a_retried_sale_does_not_repeat_the_event():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 50, 2))
    for _ in range(2):
        transaction_repository.finalize_transaction(Transaction(items=[LineItem("SKU-1", "Widget", 5.00, 1)], client_uuid="k"))
    assert _kinds().count("sale") == 1


def test_only_the_crossing_to_low_and_to_zero_is_reported():
    product_repository.create(Product("SKU-1", "Widget", 5.00, 6, 3))
    def sell(n):
        transaction_repository.finalize_transaction(Transaction(items=[LineItem("SKU-1", "Widget", 5.00, n)]))
    sell(1)  # 5 left: above the level
    assert "stock_low" not in _kinds()
    sell(2)  # 3 left: at the level -> low
    sell(1)  # 2 left: already low, no repeat
    assert _kinds().count("stock_low") == 1
    sell(2)  # 0 left -> out
    events = activity_repository.list_since(0)
    out = [e for e in events if e.kind == "stock_out"]
    assert len(out) == 1 and out[0].severity == "critical"
    assert [e for e in events if e.kind == "stock_low"][0].severity == "warning"


def test_stock_movements_are_reported_by_reason():
    product_repository.create(Product("BOX", "Carton", 5, 0, 1))
    stock_repository.receive(UNASSIGNED, "BOX", 10)
    stock_repository.dispatch(UNASSIGNED, "BOX", 4)
    kinds = _kinds()
    assert "stock_in" in kinds and "stock_written_out" in kinds


def test_severity_filters_and_counts():
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "sale", severity="info")
        activity_repository.record(conn, "refund", severity="notice")
        activity_repository.record(conn, "stock_out", severity="critical")
        activity_repository.record(conn, "x", severity="nonsense")  # unknown severity -> info
    assert [e.kind for e in activity_repository.list_recent(10, "notice")] == ["stock_out", "refund"]
    assert activity_repository.count_since(0, "notice") == 2
    assert activity_repository.count_since(activity_repository.latest_id()) == 0
    assert [e.severity for e in activity_repository.list_recent(10)][0] == "info"


def test_old_events_are_pruned_and_new_ones_stay():
    with connection.connection_scope() as conn:
        activity_repository.record(conn, "sale")
        activity_repository.record(conn, "sale")
        conn.execute("UPDATE activity_events SET at = '2000-01-01 00:00:00' WHERE id = 1")
    assert activity_repository.prune(30) == 1
    assert [e.id for e in activity_repository.list_since(0)] == [2]
