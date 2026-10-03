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
