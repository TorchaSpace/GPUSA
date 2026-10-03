"""Tests for shared.treasury - the numbers both treasury screens show."""

from __future__ import annotations

from datetime import date

import pytest

from shared.models import LedgerEntry
from shared.treasury import (
    compact_amount,
    display_status,
    due_relative_text,
    matches_status_filter,
    milestones,
    summarize,
)

TODAY = date(2026, 9, 24)


def _e(direction="in", doc_type="check", due=(9, 30), amount=100.0, status="pending", id=None):
    return LedgerEntry(
        direction=direction,
        doc_type=doc_type,
        doc_no=f"D-{id}",
        counterparty="X",
        issue_date=date(2026, 9, 1),
        due_date=date(2026, *due),
        amount=amount,
        status=status,
        id=id,
    )


@pytest.mark.parametrize(
    "entry, expected",
    [
        (_e(due=(9, 23)), "Overdue"),
        (_e(due=(9, 24)), "Pending"),  # due today is not overdue yet
        (_e(doc_type="invoice"), "Open"),
        (_e(direction="out", doc_type="invoice"), "Pending"),
        (_e(due=(9, 1), status="cleared"), "Cleared"),
        (_e(status="endorsed"), "Endorsed"),
    ],
)
def test_display_status(entry, expected):
    assert display_status(entry, TODAY) == expected


@pytest.mark.parametrize(
    "due, status, expected",
    [((9, 24), "pending", "Today"), ((9, 25), "pending", "in 1 day"), ((9, 30), "pending", "in 6 days"),
     ((9, 23), "pending", "1 day overdue"), ((9, 20), "pending", "4 days overdue"), ((9, 20), "cleared", "Settled")],
)
def test_due_relative_text(due, status, expected):
    assert due_relative_text(_e(due=due, status=status), TODAY) == expected


def test_status_filter():
    overdue, pending, cleared, endorsed = _e(due=(9, 1)), _e(), _e(status="cleared"), _e(status="endorsed")
    rows = [overdue, pending, cleared, endorsed]
    assert [r for r in rows if matches_status_filter(r, "Pending", TODAY)] == [pending]
    assert [r for r in rows if matches_status_filter(r, "Overdue", TODAY)] == [overdue]
    assert [r for r in rows if matches_status_filter(r, "Cleared", TODAY)] == [cleared, endorsed]
    assert len([r for r in rows if matches_status_filter(r, "All", TODAY)]) == 4


def test_summarize_counts_only_open_documents():
    entries = [
        _e("in", "check", (9, 26), 84200, id=1),
        _e("in", "note", (9, 29), 212000, id=2),
        _e("in", "check", (9, 18), 46750, id=3),  # overdue
        _e("in", "check", (9, 15), 58300, "cleared", id=4),  # settled - ignored
        _e("in", "invoice", (10, 20), 1000, id=5),
        _e("out", "check", (9, 25), 18400, id=6),
        _e("out", "transfer", (9, 30), 186400, id=7),
        _e("out", "note", (10, 13), 142000, id=8),
    ]
    s = summarize(entries, TODAY)

    assert s.receivables_total == 84200 + 212000 + 46750 + 1000
    assert s.receivables_checks == 84200 + 46750
    assert s.receivables_notes == 212000
    assert s.receivables_overdue == 46750
    assert s.payables_total == 18400 + 186400 + 142000
    assert s.payables_checks == 18400
    assert s.payables_transfers == 186400 + 142000  # everything outgoing that isn't a check
    assert s.net_position == round(s.receivables_total - s.payables_total, 2)
    # due today..+7 days, soonest first; the overdue one is not "due soon"
    assert [e.id for e in s.due_soon] == [6, 1, 2, 7]
    assert s.due_soon_amount == 18400 + 84200 + 212000 + 186400


def test_milestones_put_overdue_on_today_and_run_the_net():
    entries = [
        _e("in", "check", (9, 18), 100, id=1),  # overdue -> today
        _e("out", "check", (9, 24), 30, id=2),  # today
        _e("in", "note", (9, 26), 50, id=3),
        _e("out", "transfer", (9, 26), 20, id=4),
        _e("in", "check", (9, 25), 999, "cleared", id=5),  # settled - not shown
        _e("in", "check", (12, 1), 7, id=6),  # beyond 30 days
    ]
    days = milestones(entries, TODAY)

    assert len(days) == 30
    assert days[0].day == TODAY
    assert [e.id for e in days[0].incoming] == [1] and [e.id for e in days[0].outgoing] == [2]
    assert days[0].cumulative_net == 70
    assert days[1].incoming == [] and days[1].cumulative_net == 70
    assert [e.id for e in days[2].incoming] == [3] and days[2].cumulative_net == 100
    assert days[-1].cumulative_net == 100


@pytest.mark.parametrize(
    "value, expected",
    [(212000, "212k"), (84200, "84.2k"), (1840000, "1.84M"), (950, "950"), (-46750, "-46.8k")],
)
def test_compact_amount(value, expected):
    assert compact_amount(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (999_950, "1.00M"),
        (999_949, "999.9k"),
        (999_499, "999.5k"),
        (-999_950, "-1.00M"),
        (999.995, "1k"),
        (999.994, "999.99"),
        (-0.4, "0"),
        (0.4, "0"),
        (-0.0, "0"),
        (0.6, "1"),
        (12.5, "12.50"),
        (-12.5, "-12.50"),
        (12.004, "12"),
        (1000, "1k"),
        (1_000_000, "1.00M"),
        (float("nan"), "—"),
        (float("inf"), "—"),
    ],
)
def test_compact_amount_rounds_before_choosing_the_unit(value, expected):
    assert compact_amount(value) == expected
