"""shared.analytics - the numbers behind Admin's Reports and Overview."""

from datetime import date, datetime

import pytest

from shared import analytics
from shared.models import Dealership, LineItem, Transaction


def _sale(day, amount, code=None, hour=12):
    return Transaction(
        items=[LineItem(product_barcode="B1", product_name_at_sale="Box", unit_price_at_sale=amount, quantity=1)],
        created_at=datetime(2026, 9, day, hour), dealership_code=code,
    )


DEALERS = [
    Dealership("CST-04", "Harbor Point", "Coastal", "Norfolk"),
    Dealership("MET-01", "Metro Heavy", "Metro", "Columbus"),
    Dealership("VAL-02", "Riverbend", "Valley", "Knoxville"),
]


def test_month_period_and_comparison_window():
    p = analytics.period_for("month", date(2026, 9, 24))
    assert (p.start, p.end, p.days) == (date(2026, 9, 1), date(2026, 9, 24), 24)
    assert (p.prev_start, p.prev_end) == (date(2026, 8, 1), date(2026, 8, 24))
    assert p.total_days == 30


def test_month_comparison_never_spills_into_the_current_month():
    p = analytics.period_for("month", date(2026, 3, 31))
    assert (p.prev_start, p.prev_end) == (date(2026, 2, 1), date(2026, 2, 28))


def test_quarter_and_year_periods():
    q = analytics.period_for("quarter", date(2026, 5, 10))
    assert (q.start, q.prev_start, q.prev_end) == (date(2026, 4, 1), date(2026, 1, 1), date(2026, 2, 9))
    assert q.total_days == 91
    y = analytics.period_for("ytd", date(2026, 9, 24))
    assert (y.start, y.prev_start) == (date(2026, 1, 1), date(2025, 1, 1))
    assert y.prev_end == date(2025, 9, 24) and y.total_days == 365


def test_january_month_compares_against_december():
    p = analytics.period_for("month", date(2026, 1, 5))
    assert (p.prev_start, p.prev_end) == (date(2025, 12, 1), date(2025, 12, 5))


def test_unknown_period_is_rejected():
    with pytest.raises(ValueError):
        analytics.period_for("decade", date(2026, 1, 1))


def test_daily_totals_fill_empty_days_and_ignore_out_of_range():
    sales = [_sale(1, 10), _sale(1, 5.5, hour=20), _sale(3, 4), _sale(9, 99)]
    assert analytics.daily_totals(sales, date(2026, 9, 1), date(2026, 9, 4)) == [15.5, 0.0, 4.0, 0.0]
    assert analytics.daily_totals(sales, date(2026, 9, 4), date(2026, 9, 1)) == []


def test_cumulative():
    assert analytics.cumulative([1, 2.5, 0, 4]) == [1, 3.5, 3.5, 7.5]
    assert analytics.cumulative([]) == []


def test_percent_change_has_no_value_without_a_prior():
    assert analytics.percent_change(110, 100) == 10.0
    assert analytics.percent_change(80, 100) == -20.0
    assert analytics.percent_change(50, 0) is None
    assert analytics.change_text(None) == "no prior data"
    assert analytics.change_text(8.44) == "▲ 8.4%"
    assert analytics.change_text(-3.1) == "▼ 3.1%"
    assert analytics.change_text(8.44, plain=True) == "+8.4%" and analytics.change_text(-3.1, plain=True) == "-3.1%"


def test_projection():
    assert analytics.project_total(240, 24, 30) == 300.0
    assert analytics.project_total(0, 24, 30) is None
    assert analytics.project_total(100, 0, 30) is None


def test_revenue_by_region_keeps_every_region_and_unassigned():
    sales = [_sale(1, 100, "CST-04"), _sale(2, 50, "CST-04"), _sale(2, 30, "MET-01"), _sale(3, 7), _sale(3, 9, "GONE")]
    out = analytics.revenue_by_region(sales, DEALERS)
    assert out == {"Coastal": 150.0, "Metro": 30.0, "Valley": 0.0, "Unassigned": 16.0}


def test_revenue_by_region_with_no_dealerships_or_sales():
    assert analytics.revenue_by_region([], []) == {"Unassigned": 0.0}


def test_top_dealerships_ranked_with_week_and_trend():
    sales = [_sale(24, 100, "MET-01"), _sale(23, 100, "MET-01"), _sale(20, 60, "MET-01"), _sale(24, 500, "CST-04")]
    top = analytics.top_dealerships(sales, DEALERS, date(2026, 9, 24))
    assert [d.code for d in top] == ["CST-04", "MET-01"]  # VAL-02 had no sales: left out
    met = top[1]
    assert met.revenue == 260.0
    assert met.week == [0.0, 0.0, 60.0, 0.0, 0.0, 100.0, 100.0]
    assert met.trend == round((200 / 3 - 60 / 4) / (60 / 4) * 100, 1)
    assert analytics.top_dealerships(sales, DEALERS, date(2026, 9, 24), limit=1)[0].code == "CST-04"


def test_sales_today():
    sales = [_sale(24, 10), _sale(24, 5), _sale(23, 100)]
    assert analytics.sales_today(sales, date(2026, 9, 24)) == (2, 15.0)
    assert analytics.sales_today([], date(2026, 9, 24)) == (0, 0.0)


def test_period_report_sections():
    p = analytics.period_for("month", date(2026, 9, 3))
    cur = [_sale(1, 100, "CST-04"), _sale(3, 50, "MET-01")]
    prev = [Transaction(items=[LineItem("B1", "Box", 100, 1)], created_at=datetime(2026, 8, 2))]
    report = analytics.build_period_report(cur, p, DEALERS, previous=prev)
    titles = [s.title for s in report.sections]
    assert titles[:3] == ["Totals", "Revenue per day", "Revenue by region"]
    assert titles[-1] == "Per-Product Breakdown"
    totals = dict(report.sections[0].rows)
    assert totals["Revenue"] == "150.00" and totals["Change"] == "+50.0%"
    assert "Projected for the full period" in totals
    assert [r[1] for r in report.sections[1].rows] == ["100.00", "0.00", "50.00"]
    assert report.title.startswith("Revenue report · 2026-09-01 to 2026-09-03")


def test_period_report_without_a_comparison_or_sales():
    p = analytics.period_for("month", date(2026, 9, 3))
    report = analytics.build_period_report([], p, DEALERS)
    totals = dict(report.sections[0].rows)
    assert totals == {"Sales": "0", "Revenue": "0.00"}


def test_comparison_window_is_clamped_to_the_previous_quarter():
    # 30 Jun is day 91 of Q2, but Q1 only has 90 days.
    p = analytics.period_for("quarter", date(2026, 6, 30))
    assert (p.prev_start, p.prev_end) == (date(2026, 1, 1), date(2026, 3, 31))


def test_compact_amount():
    assert analytics.compact_amount(850) == "850"
    assert analytics.compact_amount(1234) == "1.2k"
    assert analytics.compact_amount(12_345) == "12k"
    assert analytics.compact_amount(4_820_000) == "4.82M"
    assert analytics.compact_amount(0) == "0"


def test_axis_ticks_end_on_a_round_number_at_or_above_the_max():
    for maximum in (0.7, 1, 37, 4820, 99_999, 123_456):
        top, ticks = analytics.axis_ticks(maximum)
        assert top >= maximum and len(ticks) == 5 and ticks[0] == 0 and ticks[-1] == top
        assert ticks == sorted(ticks)
    assert analytics.axis_ticks(0)[0] == 1.0
    assert analytics.axis_ticks(4820)[0] == 6000
    for maximum in (0.7, 37, 4820, 99_999):
        assert analytics.axis_ticks(maximum)[0] < maximum * 2  # not a wastefully tall axis


def _view(mode="cumulative", today=date(2026, 9, 3)):
    p = analytics.period_for("month", today)
    cur = [_sale(1, 100, "CST-04"), _sale(3, 50, "MET-01"), _sale(3, 10)]
    prev = [Transaction(items=[LineItem("B1", "Box", 80, 1)], created_at=datetime(2026, 8, 2))]
    return analytics.build_report_view(p, cur, prev, DEALERS, mode)


def test_view_cumulative_has_projection_that_continues_from_the_last_point():
    v = _view("cumulative")
    assert v.current == [100.0, 100.0, 160.0] and v.previous == [0.0, 80.0, 80.0]
    assert v.revenue == 160.0 and v.previous_revenue == 80.0 and v.change == 100.0
    assert v.projected_total == 1600.0  # 160 over 3 days, 30-day month
    assert len(v.projection) == 27 and v.total_points == 30
    assert v.projection[0] == round(160 + 160 / 3, 2)
    assert v.projection[-1] == 1600.0


def test_view_daily_projection_is_the_flat_daily_average():
    v = _view("daily")
    assert v.current == [100.0, 0.0, 60.0]
    assert set(v.projection) == {round(160 / 3, 2)} and len(v.projection) == 27


def test_view_closed_period_has_no_projection():
    v = _view("cumulative", today=date(2026, 9, 30))
    assert v.projection == [] and v.projected_total is None and v.total_points == 30


def test_view_regions_sorted_with_shares_and_zero_regions_hidden():
    v = _view()
    assert [r[0] for r in v.regions] == ["Coastal", "Metro", "Unassigned"]
    assert abs(sum(r[2] for r in v.regions) - 100.0) < 0.2
    assert v.regions[0] == ("Coastal", 100.0, 62.5)


def test_view_readout_covers_actual_projected_and_out_of_range_points():
    v = _view("daily")
    label, cur, prev, projected = v.readout(1)
    assert label == "Wed 02 Sep 2026" and cur == 0.0 and prev == 80.0 and not projected
    _, cur, _, projected = v.readout(10)
    assert projected and cur == round(160 / 3, 2)
    assert v.readout(29)[3] is True
    assert v.readout(99)[1:3] == (None, None)


def test_view_x_labels_are_inside_the_range_and_start_at_zero():
    v = _view()
    assert v.x_labels[0] == (0, "01 Sep")
    assert all(0 <= i < v.total_points for i, _ in v.x_labels)
    assert [i for i, _ in v.x_labels] == sorted({i for i, _ in v.x_labels})


def test_view_rejects_an_unknown_mode():
    with pytest.raises(ValueError):
        analytics.build_report_view(analytics.period_for("month", date(2026, 9, 3)), [], [], [], "weekly")
