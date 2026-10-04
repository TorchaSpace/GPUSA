"""Regression tests for database.ledger_repository."""

from __future__ import annotations

from datetime import date

import pytest

import database.connection as connection
from database import dealership_repository, ledger_repository, product_repository, purchase_order_repository
from tests.ledger_support import ACTOR, OTHER, SignedIn
from database.exceptions import DataAccessError, DuplicateLedgerDocumentError, LedgerEntryNotFoundError, LedgerEntryStateError
from shared.models import Dealership, LedgerEntry, PriceRange, Product

ledger = SignedIn(ledger_repository)


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
    # Settled entries are history: any edit is refused until it's reopened.
    entry = ledger.create(_entry())
    ledger.mark_endorsed(entry.id)
    entry = ledger.get(entry.id)
    entry.doc_type = "invoice"
    with pytest.raises(LedgerEntryStateError):
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


# --- amount validation ------------------------------------------------------


@pytest.mark.parametrize(
    "amount",
    [float("nan"), float("inf"), float("-inf"), 0.004, 0.0049, 1e300, 1_000_000_001, "12", None, True],
)
def test_create_rejects_bad_amounts_with_value_error(amount):
    with pytest.raises(ValueError):
        ledger.create(_entry(amount=amount))
    assert ledger.list_entries() == []


def test_amount_is_rounded_half_up_to_cents_before_the_positive_check():
    assert ledger.create(_entry(doc_no="A", amount=0.005)).amount == 0.01
    assert ledger.create(_entry(doc_no="B", amount=2.675)).amount == 2.68
    assert ledger.create(_entry(doc_no="C", amount=1_000_000_000)).amount == 1_000_000_000


def test_update_rejects_non_finite_amount():
    entry = ledger.create(_entry())
    entry.amount = float("nan")
    with pytest.raises(ValueError):
        ledger.update(entry)
    assert ledger.get(entry.id).amount == 84200


def test_stray_sqlite_errors_become_data_access_errors(monkeypatch):
    import sqlite3

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger_repository, "connection_scope", boom)
    for call in (lambda: ledger.get(1), lambda: ledger.list_entries(), lambda: ledger.mark_cleared(1),
                 lambda: ledger.delete(1), lambda: ledger.known_counterparties()):
        with pytest.raises(DataAccessError):
            call()


# --- state machine ----------------------------------------------------------


def test_double_clear_is_refused_and_keeps_the_first_settled_at():
    entry = ledger.create(_entry())
    first = ledger.mark_cleared(entry.id)
    with pytest.raises(LedgerEntryStateError) as info:
        ledger.mark_cleared(entry.id)
    assert info.value.status == "cleared"
    assert ledger.get(entry.id).settled_at == first.settled_at


def test_cant_clear_or_endorse_a_settled_entry():
    cleared = ledger.create(_entry(doc_no="A"))
    ledger.mark_cleared(cleared.id)
    with pytest.raises(LedgerEntryStateError):
        ledger.mark_endorsed(cleared.id)
    endorsed = ledger.create(_entry(doc_no="B"))
    ledger.mark_endorsed(endorsed.id)
    with pytest.raises(LedgerEntryStateError):
        ledger.mark_cleared(endorsed.id)
    assert ledger.get(endorsed.id).status == "endorsed"


def test_reopen_only_from_settled():
    entry = ledger.create(_entry())
    with pytest.raises(LedgerEntryStateError):
        ledger.reopen(entry.id)
    ledger.mark_cleared(entry.id)
    assert ledger.reopen(entry.id).status == "pending"
    with pytest.raises(LedgerEntryStateError):
        ledger.reopen(entry.id)
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.reopen(999)


def test_settled_entries_cant_be_edited_or_deleted_until_reopened():
    entry = ledger.create(_entry())
    ledger.mark_cleared(entry.id)
    edited = ledger.get(entry.id)
    edited.amount = 5
    with pytest.raises(LedgerEntryStateError):
        ledger.update(edited)
    with pytest.raises(LedgerEntryStateError):
        ledger.delete(entry.id)
    assert ledger.get(entry.id).amount == 84200
    ledger.reopen(entry.id)
    edited.amount = 5
    assert ledger.update(edited).amount == 5
    ledger.delete(entry.id)


def test_update_and_delete_of_a_missing_entry_raise_not_found():
    entry = ledger.create(_entry())
    ledger.delete(entry.id)
    with pytest.raises(LedgerEntryNotFoundError):
        ledger.update(entry)


# --- normalisation ----------------------------------------------------------


def test_doc_no_collides_regardless_of_case_and_spacing():
    ledger.create(_entry(doc_no="chk-1"))
    for variant in ("CHK-1", " Chk-1 "):
        with pytest.raises(DuplicateLedgerDocumentError):
            ledger.create(_entry(doc_no=variant))
    assert ledger.list_entries()[0].doc_no == "CHK-1"
    ledger.create(_entry(doc_no="chk  2"))
    with pytest.raises(DuplicateLedgerDocumentError):
        ledger.create(_entry(doc_no="CHK 2"))


def test_site_is_normalised_on_write_and_matched_case_insensitively():
    ledger.create(_entry(doc_no="A", site=" wh-01 "))
    ledger.create(_entry(doc_no="B", site="WH-02"))
    ledger.create(_entry(doc_no="C", site=None))
    assert ledger.list_entries()[0].site == "WH-01"
    assert [e.doc_no for e in ledger.list_entries(site="wh-01")] == ["A"]
    assert [e.doc_no for e in ledger.list_entries(site=" Wh-01")] == ["A"]
    assert ledger.list_entries(site="nowhere") == []


def test_legacy_lowercase_site_rows_still_match():
    created = ledger.create(_entry(doc_no="A", site="WH-01"))
    with connection.connection_scope() as conn:
        conn.execute("UPDATE ledger_entries SET site = 'wh-01' WHERE id = ?", (created.id,))
    assert [e.id for e in ledger.list_entries(site="WH-01")] == [created.id]
