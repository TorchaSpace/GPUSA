from __future__ import annotations

from datetime import datetime, timezone

from shared.formatting import age_text, format_amount, local_datetime_text, parse_db_timestamp


def test_parse_db_timestamp_is_utc():
    parsed = parse_db_timestamp("2026-09-25T07:01:46.123Z")
    assert parsed == datetime(2026, 9, 25, 7, 1, 46, 123000, tzinfo=timezone.utc)


def test_age_text_buckets():
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert age_text("2026-09-25T11:59:30.000Z", now) == "just now"
    assert age_text("2026-09-25T11:46:00.000Z", now) == "14 min"
    assert age_text("2026-09-25T07:00:00.000Z", now) == "5 h"
    assert age_text("2026-09-22T12:00:00.000Z", now) == "3 d"
    assert age_text(None, now) == "—"
    assert age_text("garbage", now) == "—"


def test_local_datetime_text_handles_missing():
    assert local_datetime_text(None) == "—"
    assert local_datetime_text("2026-09-25T07:01:46.123Z") != "—"


def test_format_amount():
    assert format_amount(1612) == "1,612.00"
    assert format_amount(38.4) == "38.40"
    assert format_amount(None) == "—"


def test_parse_amount_accepts_both_decimal_conventions():
    from shared.formatting import parse_amount

    assert parse_amount("742.50") == 742.5
    assert parse_amount("742,50") == 742.5
    assert parse_amount("1,612.00") == 1612.0
    assert parse_amount("1.612,00") == 1612.0
    assert parse_amount(" 38 ") == 38.0
    assert parse_amount("") is None
    assert parse_amount("abc") is None


import math

import pytest

PARSE_TABLE = [
    # thousands vs decimal: one separator, once, exactly 3 digits after
    ("1,234", 1234.0), ("1.234", 1234.0), ("12,345", 12345.0), ("12.345", 12345.0),
    ("123,456", 123456.0), ("999.999", 999999.0),
    # ...but a decimal when anything else holds
    ("742,50", 742.5), ("742.50", 742.5), ("0.125", 0.125), ("0,123", 0.123),
    ("1234,5", 1234.5), ("1234.567", 1234.567), ("1,2345", 1.2345), ("12,5", 12.5), ("1.5", 1.5),
    (".5", 0.5), (",5", 0.5), ("5.", 5.0), ("5,", 5.0),
    # repeated same separator = thousands
    ("1.234.567", 1234567.0), ("1,234,567", 1234567.0), ("12.345.678", 12345678.0),
    # both: the last is the decimal
    ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("1.612,00", 1612.0), ("1,612.00", 1612.0),
    ("1.234.567,89", 1234567.89), ("1,234,567.89", 1234567.89), ("1.234,567", 1234.567),
    # whitespace and sign
    (" 38 ", 38.0), ("1 234", 1234.0), ("1\u00a0234,50", 1234.5), ("-5", -5.0), ("-1,234", -1234.0),
    ("+7", 7.0), ("0", 0.0), ("007", 7.0), ("0,5", 0.5),
]

PARSE_REJECTED = [
    "", "  ", "abc", "nan", "NaN", "inf", "-inf", "Infinity", "1e999", "1e3", "1E5", "0x10", "1_000",
    "1.5.5", "1,23,456", "1,234,56", "12,34.56", "1.234.56", "1,2,3", "..", ",", ".", "-", "--5", "1-",
    "1.234,56,7", "1,234.56.7", "0.123.456", "\u0661\u0662\u0663", "12 abc", "1,,234", "$5",
]


@pytest.mark.parametrize("text, expected", PARSE_TABLE)
def test_parse_amount_table(text, expected):
    from shared.formatting import parse_amount

    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", PARSE_REJECTED)
def test_parse_amount_rejects_malformed_and_non_finite(text):
    from shared.formatting import parse_amount

    assert parse_amount(text) is None


def test_parse_amount_never_returns_a_non_finite_number():
    from shared.formatting import parse_amount

    for text in ("nan", "inf", "1e999", "9" * 400):
        value = parse_amount(text)
        assert value is None or math.isfinite(value)


def test_parse_amount_handles_none():
    from shared.formatting import parse_amount

    assert parse_amount(None) is None


@pytest.mark.parametrize(
    "value, expected",
    [(0.005, 0.01), (2.675, 2.68), (1, 1.0), (1_000_000_000, 1e9), (19.999, 20.0), (0.014, 0.01)],
)
def test_round_money_rounds_half_up_to_cents(value, expected):
    from shared.formatting import round_money

    assert round_money(value) == expected


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), 0, 0.004, -1, 1e300, 1_000_000_001, 10 ** 40, "5", None, True, [1]],
)
def test_round_money_rejects_bad_values(value):
    from shared.formatting import round_money

    with pytest.raises(ValueError):
        round_money(value)
