"""Pure helpers for staff: national ID checks/masking, place matching, history lines, periods."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from shared import national_id
from shared.staff import DEALERSHIP, WAREHOUSE, StaffPlace, duration_text, history_rows, period, total_minutes


@pytest.mark.parametrize("value", ["10000000146", "123 456 789 50", "234-567-891-38"])
def test_valid_national_ids(value):
    assert national_id.problem(value) is None
    assert national_id.validate(value) == national_id.normalize(value)


@pytest.mark.parametrize("value", ["", "1234", "1000000014a", "10000000147", "01000000146", "100000001460"])
def test_invalid_national_ids(value):
    assert national_id.problem(value)
    with pytest.raises(ValueError):
        national_id.validate(value)


def test_mask_shows_only_the_ends():
    assert national_id.mask("10000000146") == "100******46"
    assert national_id.mask("100******46") == "100******46"  # already masked (from the server)
    assert national_id.mask(None) == "—"


def test_a_place_matches_code_name_or_label_loosely():
    depot = StaffPlace(WAREHOUSE, "WH-01", "İstanbul Merkez")
    assert depot.has("Warehouse", "wh-01") and depot.has("Warehouse", "istanbul  merkez")
    assert depot.has("Warehouse", "WH-01 · İstanbul Merkez")
    assert not depot.has("Dealership", "WH-01") and not depot.has("Warehouse", "Ankara")
    assert StaffPlace(DEALERSHIP, "CST-04", "Coast").has("Dealership", "coast")


def test_history_rows_newest_first_with_durations_and_open_shifts():
    people = {"A": {"name": "Ayşe", "title": "Kasiyer", "role": "Sales & service",
                    "location_type": "Dealership", "location_name": "CST-04"}}
    records = [
        {"badge_id": "A", "name": "Ayşe", "check_in_at": "2026-11-02T06:00:00.000Z", "check_out_at": "2026-11-02T14:30:00.000Z"},
        {"badge_id": "A", "name": "Ayşe", "check_in_at": "2026-11-03T06:00:00.000Z", "check_out_at": None},
        {"badge_id": "Z", "name": "Not listed", "check_in_at": "2026-11-03T06:00:00.000Z", "check_out_at": None},
    ]
    now = datetime(2026, 11, 3, 7, 15, tzinfo=timezone.utc)
    rows = history_rows(records, people, now)
    assert [r["minutes"] for r in rows] == [75, 510]
    assert [r["open"] for r in rows] == [True, False]
    assert total_minutes(rows) == 585
    assert duration_text(510) in ("8 h 30 min", "8 sa 30 dk")


def test_periods():
    today = date(2026, 11, 8)  # a Sunday
    assert period("today", today) == (today, today)
    assert period("yesterday", today) == (date(2026, 11, 7),) * 2
    assert period("week", today) == (date(2026, 11, 2), today)
    assert period("month", today) == (date(2026, 11, 1), today)
