"""attendance_repository.list_roster: open shifts from earlier days, forgotten
check-outs, summed hours, night shifts - with local-day boundaries."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

import database.connection as connection
from database import attendance_repository, employee_repository
from shared import overview
from shared.formatting import local_clock_text, to_db_timestamp
from shared.models import Employee

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def person() -> Employee:
    emp = Employee("B-1", "Elena Varga", "Operations", "Warehouse", "Merkez Depo")
    employee_repository.create(emp)
    return emp


def _local(day: date, hour: int, minute: int = 0) -> str:
    """A DB timestamp for `hour:minute` LOCAL time on `day`."""
    return to_db_timestamp(datetime.combine(day, time(hour, minute)))


def _record(check_in: str, check_out: str | None, badge_id: str = "B-1") -> None:
    with connection.connection_scope() as conn:
        employee_id = conn.execute("SELECT id FROM employees WHERE badge_id = ?", (badge_id,)).fetchone()[0]
        conn.execute("INSERT INTO attendance_records (employee_id, check_in_at, check_out_at) VALUES (?, ?, ?)",
                     (employee_id, check_in, check_out))


def _roster(for_date=None, now=None):
    return attendance_repository.list_roster(for_date, now=now)[0]


def test_an_open_shift_from_yesterday_still_shows_present_and_is_flagged(person):
    yesterday = date.today() - timedelta(days=1)
    _record(_local(yesterday, 12), None)
    row = _roster(now=datetime.combine(date.today(), time(9)).astimezone())  # 21 h later
    assert row["status"] == "Present"
    assert row["check_in_at"] == _local(yesterday, 12) and row["check_out_at"] is None
    assert row["long_open"] is True


def test_long_open_flag_uses_the_sixteen_hour_threshold(person):
    start = datetime(2026, 3, 10, 6, 0).astimezone()
    _record(to_db_timestamp(start), None)
    on_day = "2026-03-10"
    within = _roster(on_day, now=start + timedelta(hours=15, minutes=59))
    beyond = _roster(on_day, now=start + timedelta(hours=16, minutes=1))
    assert within["status"] == "Present" and within["long_open"] is False
    assert beyond["status"] == "Present" and beyond["long_open"] is True


def test_a_fresh_open_shift_is_present_and_not_flagged(person):
    attendance_repository.check_in("B-1")
    row = _roster()
    assert row["status"] == "Present" and row["long_open"] is False and row["hours"] is None


def test_hours_add_up_all_completed_records_of_the_day(person):
    day = date.today() - timedelta(days=1)
    _record(_local(day, 8), _local(day, 10))      # 2 h
    _record(_local(day, 11), _local(day, 14))     # 3 h
    _record(_local(day, 15), _local(day, 15, 30))  # 0.5 h
    row = _roster(day.isoformat())
    assert row["status"] == "Checked out" and row["hours"] == 5.5
    # the shown stamps are the latest record's
    assert row["check_in_at"] == _local(day, 15) and row["check_out_at"] == _local(day, 15, 30)


def test_present_with_earlier_completed_work_shows_the_partial_hours(person):
    today = date.today()
    _record(_local(today, 0, 5), _local(today, 0, 35))  # 0.5 h earlier today
    _record(_local(today, 0, 40), None)
    row = _roster(today.isoformat(), now=datetime.combine(today, time(1)).astimezone())
    assert row["status"] == "Present" and row["hours"] == 0.5


def test_a_night_shift_counts_on_both_days_clipped_to_each(person):
    day = date.today() - timedelta(days=2)
    _record(_local(day, 22), _local(day + timedelta(days=1), 6))  # 22:00 -> 06:00: 2 h + 6 h
    first = _roster(day.isoformat())
    second = _roster((day + timedelta(days=1)).isoformat())
    third = _roster((day + timedelta(days=2)).isoformat())
    assert first["status"] == "Checked out" and first["hours"] == 2.0
    assert second["status"] == "Checked out" and second["hours"] == 6.0
    assert second["check_in_at"] == _local(day, 22)  # the record that carries into the day
    assert third["status"] == "Off" and third["hours"] is None


def test_the_day_is_the_local_day_not_the_utc_day(person):
    day = date.today() - timedelta(days=1)
    _record(_local(day, 0, 30), _local(day, 1, 30))  # just after local midnight (still the previous UTC date in Istanbul)
    assert _roster(day.isoformat())["hours"] == 1.0
    assert _roster((day - timedelta(days=1)).isoformat())["status"] == "Off"


def test_roster_lists_everyone_and_keeps_the_keys_other_screens_read(person):
    employee_repository.create(Employee("B-2", "Zeynep", "Logistics", "Dealership", "Coastal"))
    rows = attendance_repository.list_roster()
    assert [r["badge_id"] for r in rows] == ["B-1", "B-2"]
    for key in ("badge_id", "name", "title", "role", "location_type", "location_name", "is_active",
                "status", "check_in_at", "check_out_at", "hours", "long_open"):
        assert key in rows[0]


def test_overview_counts_follow_the_corrected_roster(person):
    employee_repository.create(Employee("B-2", "Zeynep", "Logistics", "Dealership", "Coastal"))
    employee_repository.create(Employee("B-3", "Can", "Logistics", "Dealership", "Coastal"))
    yesterday = date.today() - timedelta(days=1)
    _record(_local(yesterday, 22), None, "B-1")  # forgotten check-out: still on site
    today = date.today()
    _record(_local(today, 0, 1), _local(today, 0, 2), "B-2")
    summary = overview.attendance_today(attendance_repository.list_roster())
    assert (summary.on_site, summary.checked_out, summary.not_in) == (1, 1, 1)


def test_clock_text_is_local_time_and_shows_the_date_for_other_days():
    stamp = _local(date(2026, 3, 10), 22, 15)
    assert local_clock_text(stamp, now=datetime(2026, 3, 10, 23, 0).astimezone()) == "22:15"
    assert local_clock_text(stamp, now=datetime(2026, 3, 11, 9, 0).astimezone()) == "10.03.2026 22:15"
    assert local_clock_text(None) == "—" and local_clock_text("garbage") == "—"


def test_list_punches_gives_todays_events_newest_first(person):
    attendance_repository.check_in("B-1")
    attendance_repository.check_out("B-1")
    attendance_repository.check_in("B-1")
    punches = attendance_repository.list_punches()
    assert [p["action"] for p in punches] == ["IN", "OUT", "IN"]
    assert {p["name"] for p in punches} == {"Elena Varga"}
    assert attendance_repository.list_punches(limit=2) == punches[:2]
    assert attendance_repository.list_punches(for_date="2001-01-01") == []
