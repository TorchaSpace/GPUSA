"""Renaming a warehouse must not orphan its purchase orders and ledger entries: both are stamped
with the "CODE · NAME" text, so site filters and Floor receiving compare the code only."""

from __future__ import annotations

from datetime import date

import pytest

import database.connection as connection
from database import ledger_repository, purchase_order_repository as po_repo, stock_repository, warehouse_repository
from shared.models import LedgerEntry, Warehouse
from shared.warehousing import site_code
from tests.po_support import DEPOT, SITE, WH1, seed_people, seed_world, sent_order
from tests.stock_invariant import assert_totals_consistent


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _rename():
    warehouse_repository.update(Warehouse(code="WH-01", name="Gebze Depo", city="Tuzla", capacity_units=1000, docks=4))


def test_site_code():
    assert site_code("WH-01 · İSTANBUL MERKEZ") == "WH-01"
    assert site_code("wh-02") == "WH-02"
    assert site_code("") is None and site_code(None) is None


def test_rename_keeps_orders_and_ledger_entries_findable():
    seed_world()
    seed_people()
    sent_order()
    ledger_repository.create(
        LedgerEntry("out", "invoice", "INV-1", "Kuzey", date(2026, 10, 1), date(2026, 11, 1), 100.0, site=SITE),
        DEPOT)
    _rename()
    new_site = "WH-01 · Gebze Depo"
    assert len(po_repo.list_orders(site=new_site)) == 1
    assert len(po_repo.list_orders(site=SITE)) == 1
    assert len(ledger_repository.list_entries(site=new_site)) == 1


def test_receive_delivery_after_rename_settles_the_order():
    seed_world()
    seed_people()
    order = sent_order(quantity=100)
    _rename()
    assert po_repo.receive_delivery(WH1, "PLT-4410", 100, DEPOT) == [order.number]
    assert po_repo.list_orders(status="received")[0].number == order.number
    # nothing is awaited any more: a second delivery is only a plain receipt
    assert po_repo.receive_delivery(WH1, "PLT-4410", 5, DEPOT) == []
    assert_totals_consistent()
