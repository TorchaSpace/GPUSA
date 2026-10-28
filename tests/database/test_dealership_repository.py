"""Regression tests for database.dealership_repository - mirrors
test_product_repository.py's shape/fixture pattern.
"""

from __future__ import annotations

import pytest

import database.connection as connection
from database import dealership_repository
from database.exceptions import DealershipNotFoundError, DuplicateDealershipCodeError
from shared.models import Dealership


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_backend.db"
    monkeypatch.setattr(connection, "get_db_path", lambda: db_path)
    monkeypatch.setattr(connection, "_initialized", False)


def _make_dealership(code: str = "CST-04", **overrides) -> Dealership:
    fields = dict(
        code=code,
        name="Harbor Point Equipment",
        region="Coastal",
        city="Norfolk, VA",
        manager_name="L. Brandt",
        is_active=True,
    )
    fields.update(overrides)
    return Dealership(**fields)


def test_create_then_get_by_code_roundtrips():
    dealership = _make_dealership()

    dealership_repository.create(dealership)
    fetched = dealership_repository.get_by_code(dealership.code)

    assert fetched.code == dealership.code
    assert fetched.name == dealership.name
    assert fetched.region == dealership.region
    assert fetched.city == dealership.city
    assert fetched.manager_name == dealership.manager_name
    assert fetched.is_active is True
    assert fetched.id is not None  # surrogate PK assigned by SQLite


def test_create_duplicate_code_raises():
    dealership_repository.create(_make_dealership())

    with pytest.raises(DuplicateDealershipCodeError):
        dealership_repository.create(_make_dealership(name="A Different Name"))


def test_create_invalid_region_raises():
    with pytest.raises(ValueError):
        dealership_repository.create(_make_dealership(region="Nowhere"))


def test_update_invalid_region_raises():
    dealership_repository.create(_make_dealership())

    with pytest.raises(ValueError):
        dealership_repository.update(_make_dealership(region="Nowhere"))


def test_get_by_code_missing_raises():
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.get_by_code("does-not-exist")


def test_list_all_orders_by_name():
    dealership_repository.create(_make_dealership(code="B-1", name="Bravo Motors"))
    dealership_repository.create(_make_dealership(code="A-1", name="Alpha Equipment"))

    result = dealership_repository.list_all()

    assert [d.name for d in result] == ["Alpha Equipment", "Bravo Motors"]


def test_update_changes_fields():
    original = _make_dealership()
    dealership_repository.create(original)

    dealership_repository.update(
        _make_dealership(
            name="Renamed Motors",
            region="Metro",
            city="Columbus, OH",
            manager_name="J. Whitaker",
            is_active=False,
        )
    )

    fetched = dealership_repository.get_by_code(original.code)
    assert fetched.name == "Renamed Motors"
    assert fetched.region == "Metro"
    assert fetched.city == "Columbus, OH"
    assert fetched.manager_name == "J. Whitaker"
    assert fetched.is_active is False


def test_update_missing_raises():
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.update(_make_dealership(code="does-not-exist"))


def test_delete_removes_dealership():
    dealership = _make_dealership()
    dealership_repository.create(dealership)

    dealership_repository.delete(dealership.code)

    with pytest.raises(DealershipNotFoundError):
        dealership_repository.get_by_code(dealership.code)


def test_delete_missing_raises():
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.delete("does-not-exist")


def _raw_insert(code: str) -> None:
    with connection.connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO dealerships (code, name, region, city, is_active) VALUES (?, 'Legacy', 'Coastal', 'X', 1)",
            (code,),
        )
        conn.execute("COMMIT")


def test_canonical_code_returns_the_stored_code():
    _raw_insert("ist-01")  # a row saved before codes were upper-cased
    dealership_repository.create(_make_dealership("CST-04"))

    assert dealership_repository.canonical_code("IST-01") == "ist-01"
    assert dealership_repository.canonical_code(" cst-04 ") == "CST-04"
    assert dealership_repository.canonical_code("nope-9") == "NOPE-9"  # unknown: just normalised


def test_delete_finds_a_legacy_lower_case_row_by_any_case():
    _raw_insert("ist-01")

    dealership_repository.delete("IST-01")

    assert dealership_repository.list_all() == []
    with pytest.raises(DealershipNotFoundError):
        dealership_repository.delete("IST-01")
