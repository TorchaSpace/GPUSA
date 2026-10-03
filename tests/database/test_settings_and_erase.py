"""Settings saved atomically; "start over" needs its confirmation word."""

from __future__ import annotations

import pytest

import database.connection as connection
from database import account_repository as accounts, employee_repository, settings_repository
from shared import store_settings as ss
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)
    monkeypatch.setattr("shared.paths.get_db_path", lambda: tmp_path / "t.db")


def test_profile_and_language_are_saved_together():
    profile = ss.StoreProfile("Acme Parts", ("1 Main St",))
    settings_repository.save_profile_and_language(profile, "tr")
    assert settings_repository.load_store_profile() == profile
    assert settings_repository.safe_language() == "tr"


def test_an_invalid_language_saves_nothing_at_all():
    settings_repository.save_profile_and_language(ss.StoreProfile("Before", ()), "en")
    with pytest.raises(ValueError):
        settings_repository.save_profile_and_language(ss.StoreProfile("After", ()), "xx")
    assert settings_repository.load_store_profile().name == "Before"
    assert settings_repository.safe_language() == "en"


def test_a_failure_part_way_rolls_both_back(monkeypatch):
    settings_repository.save_profile_and_language(ss.StoreProfile("Before", ()), "en")
    real = ss.profile_to_values

    def broken(profile):
        values = real(profile)
        values["bad"] = None  # app_settings.value is NOT NULL: fails after the profile rows were written
        return values

    monkeypatch.setattr(ss, "profile_to_values", broken)
    with pytest.raises(Exception):
        settings_repository.save_profile_and_language(ss.StoreProfile("After", ()), "tr")
    monkeypatch.setattr(ss, "profile_to_values", real)
    assert settings_repository.load_store_profile().name == "Before"
    assert settings_repository.safe_language() == "en"


def test_erase_needs_the_confirmation_word_and_touches_nothing_without_it(tmp_path):
    accounts.create_first_admin("A-1", "Erol", "482913")
    for bad in (None, "", "yes", "DELETE", "si"):
        with pytest.raises(ValueError):
            connection.erase_all_data(bad)
    assert accounts.admin_exists()
    assert list(tmp_path.glob("*.backup-*")) == []  # no backup made for a refused call either


@pytest.mark.parametrize("word", ["SIL", "sil", " Sil ", "ERASE", "erase"])
def test_erase_with_either_word_keeps_a_backup(tmp_path, word):
    accounts.create_first_admin("A-1", "Erol", "482913")
    employee_repository.create(Employee("B-2", "Selin", "Sales & service", "Dealership", "Harbor Point"))
    backup = connection.erase_all_data(word)
    assert backup.exists() and not accounts.admin_exists() and employee_repository.list_all() == []


def test_two_erases_in_the_same_second_do_not_collide(tmp_path):
    accounts.create_first_admin("A-1", "Erol", "482913")
    first = connection.erase_all_data("SIL")
    second = connection.erase_all_data("ERASE")
    assert first != second and first.exists() and second.exists()
