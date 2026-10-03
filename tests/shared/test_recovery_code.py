from datetime import datetime, timezone

from shared import recovery_code as rc


def test_generated_codes_are_unambiguous_and_unique():
    codes = {rc.generate() for _ in range(200)}
    assert len(codes) == 200
    for code in codes:
        assert len(code) == 19 and code.count("-") == 3
        assert set(code.replace("-", "")) <= set(rc.ALPHABET)
        assert not set("01ILO") & set(code)


def test_normalize_ignores_case_dashes_and_spaces():
    assert rc.normalize(" k7qd-2mxh 9pra-tc4w ") == "K7QD2MXH9PRATC4W"
    assert rc.looks_complete("k7qd-2mxh-9pra-tc4w") and not rc.looks_complete("k7qd")


def test_lock_after_the_fifth_wrong_guess_only():
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    assert all(rc.lock_expiry(n, now) is None for n in range(1, rc.MAX_FAILED_ATTEMPTS))
    assert rc.lock_expiry(rc.MAX_FAILED_ATTEMPTS, now) == datetime(2026, 10, 3, 12, 15, tzinfo=timezone.utc)
