"""Regression tests for database.ledger_repository."""

from __future__ import annotations

from datetime import date

import pytest

import database.connection as connection
from database import dealership_repository, ledger_repository as ledger, product_repository, purchase_order_repository
from database.exceptions import DuplicateLedgerDocumentError, LedgerEntryNotFoundError
from shared.models import Dealership, LedgerEntry, PriceRange, Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


def _entry(**overrides) -> LedgerEntry:
    fields = dict(
        direction="in",
        doc_type="check",
        doc_no="CHK-40211",
        counterparty="Harbor Point Equipment",
        detail="First Coastal Bank",
        issue_date=date(2026, 9, 2),
        due_date=date(2026, 9, 26),
        amount=84200,
    )
    fields.update(overrides)
    return LedgerEntry(**fields)


def test_create_and_get_roundtrip():
    created = ledger.create(_entry(site="  "))

    fetched = ledger.get(created.id)
    assert fetched.doc_no == "CHK-40211"
    assert fetched.issue_date == date(2026, 9, 2)
    assert fetched.due_date == date(2026, 9, 26)
    assert fetched.status == "pending"
    assert fetched.site is None  # blank site = company level
    assert fetched.signed_amount == 84200


def test_outgoing_signed_amount_is_negative():
    assert ledger.create(_entry(direction="out", doc_no="OUT-1")).signed_amount == -84200


@pytest.mark.parametrize(
    "overrides",
    [
        dict(doc_no=" "),
        dict(counterparty=""),
        dict(amount=0),
        dict(amount=-5),
        dict(due_date=date(2026, 9, 1)),  # before issue
        dict(doc_type="bitcoin"),
        dict(direction="sideways"),
    ],
)
def test_create_validates(overrides):
    with pytest.raises(ValueError):
        ledger.create(_entry(**overrides))
    assert ledger.list_entries() == []


def test_same_document_twice_is_refused_but_other_direction_is_fine():
    ledger.create(_entry())
    with pytest.raises(DuplicateLedgerDocumentError):
        ledger.create(_entry())
    ledger.create(_entry(direction="out"))  # our own check with the same number
    assert len(ledger.list_entries()) == 2


def test_list_orders_by_due_date_and_filters():
    late = ledger.create(_entry(doc_no="A", due_date=date(2026, 10, 9), site="WH-01"))
    soon = ledger.create(_entry(doc_no="B", due_date=date(2026, 9, 20), direction="out", site="WH-01"))
    ledger.create(_entry(doc_no="C", due_date=date(2026, 9, 25), site="WH-02"))

    assert [e.doc_no for e in ledger.list_entries()] == ["B", "C", "A"]
    assert [e.id for e in ledger.list_entries(site="WH-01")] == [soon.id, late.id]
    assert [e.id for e in ledger.list_entries(direction="out")] == [soon.id]


def test_update_edits_fields():
    entry = ledger.create(_entry())
    entry.amount = 90000
    entry.counterparty = "Metro Heavy Parts"

    updated = ledger.update(entry)

    assert (updated.amount, updated.counterparty) == (90000, "Metro Heavy Parts")


def test_update_into_duplicate_is_refused():
    ledger.create(_entry(doc_no="A"))
    other = ledger.create(_entry(doc_no="B"))
    other.doc_no = "A"
    with pytest.raises(DuplicateLedgerDocumentError):
        ledger.update(other)


def test_clear_endorse_and_reopen():
    entry = ledger.create(_entry())

    cleared = ledger.mark_cleared(entry.id)
    assert (cleared.status, cleared.is_open) == ("cleared", False)
    assert cleared.settled_at is not None

    reopened = ledger.reopen(entry.id)
    assert (reopened.status, reopened.settled_at) == ("pending", None)

    assert ledger.mark_endorsed(entry.id).status == "endorsed"


@pytest.mark.parametrize("overrides", [dict(direction="out"), dict(doc_type="invoice"), dict(doc_type="transfer")])
def test_only_received_checks_and_notes_can_be_endorsed(overrides):
    entry = ledger.create(_entry(**overrides))
    with pytest.raises(ValueError):
        ledger.mark_endorsed(entry.id)
    assert ledger.get(entry.id).status == "pending"


def test_an_endorsed_check_cant_be_edited_into_an_invoice():
    entry = ledger.create(_entry())
    ledger.mark_endorsed(entry.id)
    entry = ledger.get(entry.id)
    entry.doc_type = "invoice"
    with pytest.raises(ValueError):
        ledger.update(entry)


def test_delete_and_missing_ids():
    entry = ledger.create(_entry())
    ledger.delete(entry.id)
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.get(entry.id)
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.delete(entry.id)
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.mark_cleared(entry.id)


def test_known_counterparties_merges_every_source():
    ledger.create(_entry(counterparty="Harbor Point Equipment"))
    dealership_repository.create(
        Dealership(code="001", name="harbor point equipment", region="Coastal", city="X")
    )
    dealership_repository.create(Dealership(code="002", name="Riverbend Machinery", region="Valley", city="Y"))
    product_repository.create(Product("PLT-4410", "Wrap", 1, 1))
    purchase_order_repository.set_price_range(PriceRange("PLT-4410", 1, 2, "Kuzey Ambalaj A.Ş."))
    purchase_order_repository.submit("PLT-4410", "Etiket Pro", 1, 1.5, "WH-01")

    names = ledger.known_counterparties()

    assert names == ["Etiket Pro", "Harbor Point Equipment", "Kuzey Ambalaj A.Ş.", "Riverbend Machinery"]
