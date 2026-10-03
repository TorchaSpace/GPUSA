"""shared.auth - roles, PIN rules and hashing."""

import pytest

from shared import auth


def test_default_hashing_is_slow_on_purpose():
    assert auth.DEFAULT_PBKDF2_ITERATIONS >= 100_000


def test_hash_and_verify():
    stored = auth.hash_pin("482913")
    assert stored.startswith("pbkdf2_sha256$1000$") and "482913" not in stored
    assert auth.verify_pin("482913", stored)
    assert not auth.verify_pin("482914", stored)
    assert not auth.verify_pin("", stored)
    assert auth.hash_pin("482913") != stored  # salted
    assert not auth.verify_pin("482913", "garbage")


@pytest.mark.parametrize("pin, role, ok", [
    ("4829", "cashier", True), ("482", "cashier", False), ("4829", "admin", False), ("482913", "admin", True),
    ("12a4", "cashier", False), ("1111", "cashier", False), ("1234", "cashier", False), ("9876", "cashier", False),
    ("٤٨٢٩", "cashier", False), ("4" * 13, "cashier", False),
])
def test_pin_rules(pin, role, ok):
    assert (auth.pin_problem(pin, role) is None) is ok


def test_role_areas():
    assert auth.can_open("admin", auth.AREA_ADMIN) and auth.can_open("admin", auth.AREA_POS)
    assert auth.can_open("admin", auth.AREA_DEPOT_CONSOLE)
    assert auth.can_open("depot_manager", auth.AREA_DEPOT_CONSOLE) and not auth.can_open("depot_manager", auth.AREA_POS)
    assert auth.can_open("cashier", auth.AREA_POS) and not auth.can_open("cashier", auth.AREA_ADMIN)


def test_session_helpers():
    s = auth.Session(1, "B-1", "Murat Yılmaz", "cashier", "2026-01-01T00:00:00.000Z", auth.AREA_POS, "POS 001")
    assert (s.first_name, s.initials, s.role_label, s.actor.label) == ("Murat", "MY", "Cashier", "Murat Yılmaz · B-1")
