import pytest

import database.connection as connection
from database import settings_repository as repo
from shared import store_settings as ss


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    monkeypatch.setattr("shared.paths.get_db_path", lambda: tmp_path / "t.db")


def test_default_is_none_then_saved():
    assert repo.safe_currency() is None
    repo.save_currency("TRY")
    assert repo.safe_currency() == "TRY"


def test_unknown_currency_refused():
    with pytest.raises(ValueError):
        repo.save_currency("ZZZ")
    with pytest.raises(ValueError):
        repo.save_general(ss.StoreProfile("A", ()), "en", "ZZZ")


def test_save_general_stores_currency_with_profile():
    repo.save_general(ss.StoreProfile("Acme", ()), "tr", "EUR")
    assert repo.safe_currency() == "EUR" and repo.safe_language() == "tr"
    repo.save_general(ss.StoreProfile("Acme", ()), "tr")  # None keeps it
    assert repo.safe_currency() == "EUR"
