"""Revenue analytics for Admin's Reports and Overview pages.

Pure functions - no Qt, no database - so every number the dashboard shows
is computed in one testable place and the page only draws it (the same
split as shared/treasury.py and shared/distribution.py).

What the revenue breakdown is built from, and what it deliberately is not:
the admin mockup splits revenue into "Wholesale / Dealer / Direct"
channels, but nothing in this system records a sales channel. What IS
recorded on every sale is the dealership whose shelf it came off
(transactions.dealership_code), so the breakdown here is by dealership
REGION (Metro / Coastal / Valley) plus "Unassigned" for sales from a till
with no dealership. If a real channel is ever recorded, only
revenue_by_region()'s grouping key changes.

Dates: transactions arrive from transaction_repository.list_between with
created_at already converted to the machine's LOCAL time, so a sale's
calendar day is the day the shop saw it (not the UTC day the database
stamped).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from shared.builders.report_builder import (
    PRODUCT_SECTION_TITLE,
    NumericText,
    ProfitSummary,
    ReportDocument,
    ReportSection,
    amount_text,
    build_sales_report,
    cents_to_amount,
    count_text,
    margin_cell,
    profit_cell,
    profit_summary,
    profit_totals_rows,
    transaction_cents,
)
from shared.formatting import day_month_text, format_number, localize_number, long_date_text
from shared.i18n import tr
from shared.models import Dealership, Transaction

UNASSIGNED_REGION = "Unassigned"

PERIOD_KEYS = ("month", "quarter", "ytd")
PERIOD_LABELS = {"month": "Month", "quarter": "Quarter", "ytd": "Year to date"}


@dataclass(frozen=True)
class Period:
    """A reporting window, both ends inclusive, plus the window it is
    compared against.

    Comparison rule (like with like): both windows cover the SAME NUMBER
    OF WHOLE DAYS, counted from the first day of their month / quarter /
    year. Normally that is every elapsed day including today, but if the
    previous period is shorter than the elapsed days (31 Mar vs a 28-day
    February) the comparison is clamped to the previous period's length
    on BOTH sides: `compare_end` is the last day of the current window
    that takes part (28 Mar), `prev_end` the last day of the previous one
    (28 Feb). Today counts as a day even though it is still in progress,
    so early in the day the change reads low; the screens say "so far".
    The headline revenue and the projection always use the whole window.
    """

    key: str
    label: str
    start: date
    end: date
    prev_start: date
    prev_end: date
    compare_end: date | None = None  # None: the whole window is compared

    @property
    def comparable_end(self) -> date:
        return self.end if self.compare_end is None else self.compare_end

    @property
    def comparable_days(self) -> int:
        return (self.comparable_end - self.start).days + 1

    @property
    def is_clamped(self) -> bool:
        return self.comparable_end != self.end

    @property
    def week_start(self) -> date:
        """First day the 7-day sparklines need: the window's own start, or
        6 days before its end when that is earlier (early in a month the
        week reaches back into the previous one)."""
        return min(self.start, self.end - timedelta(days=6))

    @property
    def week_start_datetime(self) -> datetime:
        return datetime.combine(self.week_start, datetime.min.time())

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def end_exclusive(self) -> datetime:
        return datetime.combine(self.end + timedelta(days=1), datetime.min.time())

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.start, datetime.min.time())

    @property
    def prev_end_exclusive(self) -> datetime:
        return datetime.combine(self.prev_end + timedelta(days=1), datetime.min.time())

    @property
    def prev_start_datetime(self) -> datetime:
        return datetime.combine(self.prev_start, datetime.min.time())

    @property
    def total_days(self) -> int:
        """Days in the whole month / quarter / year this period belongs to
        (what a projection extrapolates to)."""
        if self.key == "month":
            return calendar.monthrange(self.start.year, self.start.month)[1]
        if self.key == "quarter":
            first = self.start
            last_month = first.month + 2
            return (date(first.year, last_month, calendar.monthrange(first.year, last_month)[1]) - first).days + 1
        return 366 if calendar.isleap(self.start.year) else 365


def _shift_months(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def period_for(key: str, today: date) -> Period:
    """The window for "Month" / "Quarter" / "Year to date" ending today,
    with its comparison window (same elapsed length, one period earlier)."""
    if key not in PERIOD_KEYS:
        raise ValueError(f"period must be one of {PERIOD_KEYS!r}, got {key!r}")
    if key == "month":
        start = date(today.year, today.month, 1)
        py, pm = _shift_months(today.year, today.month, -1)
    elif key == "quarter":
        first_month = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, first_month, 1)
        py, pm = _shift_months(today.year, first_month, -3)
    else:
        start = date(today.year, 1, 1)
        py, pm = today.year - 1, 1
    elapsed_days = (today - start).days + 1
    prev_start = date(py, pm, 1)
    # Never spill past the end of the previous month / quarter / year (e.g.
    # 31 Mar vs a 28-day February): both windows are cut to its length.
    span_months = {"month": 1, "quarter": 3, "ytd": 12}[key]
    last_y, last_m = _shift_months(py, pm, span_months - 1)
    prev_length = (_clamp_day(last_y, last_m, 31) - prev_start).days + 1
    compared = min(elapsed_days, prev_length)
    prev_end = prev_start + timedelta(days=compared - 1)
    compare_end = None if compared == elapsed_days else start + timedelta(days=compared - 1)
    return Period(key, PERIOD_LABELS[key], start, today, prev_start, prev_end, compare_end)


# --- Series ---------------------------------------------------------------


def daily_totals(transactions: list[Transaction], start: date, end: date) -> list[float]:
    """Revenue per calendar day from `start` to `end` inclusive (zero for
    a day with no sales), oldest first."""
    if end < start:
        return []
    totals = [0] * ((end - start).days + 1)  # whole cents
    for transaction in transactions:
        if transaction.created_at is None:
            continue
        index = (transaction.created_at.date() - start).days
        if 0 <= index < len(totals):
            totals[index] += transaction_cents(transaction)
    return [cents_to_amount(value) for value in totals]


def revenue_between(transactions: list[Transaction], start: date, end: date) -> float:
    """Revenue of the sales whose local calendar day is in [start, end]."""
    return cents_to_amount(sum(
        transaction_cents(t) for t in transactions if t.created_at is not None and start <= t.created_at.date() <= end
    ))


def profit_between(transactions: list[Transaction], start: date, end: date) -> ProfitSummary:
    """Revenue / known-cost profit of the sales whose local calendar day is
    in [start, end] - the profit twin of revenue_between."""
    return profit_summary(t for t in transactions if t.created_at is not None and start <= t.created_at.date() <= end)


def margin_display(margin: float | None) -> str:
    """"39.0%" for the screen (a decimal comma in Turkish), "—" when there is
    no margin to show."""
    return "—" if margin is None else f"{format_number(margin, 1)}%"


def period_comparison(
    period: Period, transactions: list[Transaction], previous: list[Transaction]
) -> tuple[float, float, float | None]:
    """(this period's revenue over the compared days, the previous
    period's revenue over the same number of days, percent change). The
    headline revenue is NOT this - it covers the whole window."""
    current = revenue_between(transactions, period.start, period.comparable_end)
    prior = revenue_between(previous, period.prev_start, period.prev_end)
    return current, prior, percent_change(current, prior)


def cumulative(values: list[float]) -> list[float]:
    running = 0.0
    out = []
    for value in values:
        running += value
        out.append(round(running, 2))
    return out


def percent_change(current: float, previous: float) -> float | None:
    """Percent change from `previous` to `current`; None when there is no
    previous figure to compare against (never a divide-by-zero, never a
    made-up "+100%")."""
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1) + 0.0  # + 0.0: -0.0 becomes 0.0


def change_text(change: float | None, plain: bool = False) -> str:
    """"▲ 8.4%" for the screen; plain=True gives "+8.4%" for exports (no
    triangle glyphs needed in any font). The direction is decided from the
    ROUNDED figure, so a change that displays as 0.0% is neutral ("• 0.0%"),
    never an up or down arrow."""
    if change is None:
        return tr("analytics.no_prior") if not plain else "no prior data"
    shown = round(change, 1) + 0.0
    if shown == 0:
        return "0.0%" if plain else "• " + localize_number("0.0%")
    if plain:
        return f"{shown:+.1f}%"
    return f"{'▲' if shown > 0 else '▼'} {localize_number(f'{abs(shown):.1f}')}%"


def change_direction(change: float | None) -> int | None:
    """+1 up, -1 down, 0 neutral (rounds to 0.0%), None when unknown."""
    if change is None:
        return None
    shown = round(change, 1)
    return (shown > 0) - (shown < 0)


def change_cell(change: float | None) -> str:
    """The export cell for a change: numeric (a fraction, shown as a
    percentage) in Excel, "+8.4%" text elsewhere."""
    text = change_text(change, plain=True)
    if change is None:
        return text
    return NumericText(text, round(change, 1) / 100, "+0.0%;-0.0%;0.0%")


def project_total(total_so_far: float, elapsed_days: int, total_days: int) -> float | None:
    """Straight-line projection of the period's close from its daily
    average so far; None before there is anything to extrapolate from."""
    if elapsed_days <= 0 or total_so_far <= 0:
        return None
    return round(total_so_far / elapsed_days * total_days, 2)


# --- Breakdowns -----------------------------------------------------------


def revenue_by_region(transactions: list[Transaction], dealerships: list[Dealership]) -> dict[str, float]:
    """Revenue per dealership region. A sale whose dealership code is
    missing or no longer exists falls under "Unassigned". Regions with no
    sales are still listed (at 0) so the breakdown keeps a stable shape."""
    region_of = {d.code: d.region for d in dealerships}
    totals: dict[str, int] = {region: 0 for region in sorted({d.region for d in dealerships})}
    for transaction in transactions:
        region = region_of.get(transaction.dealership_code or "", UNASSIGNED_REGION)
        totals[region] = totals.get(region, 0) + transaction_cents(transaction)
    if UNASSIGNED_REGION not in totals:
        totals[UNASSIGNED_REGION] = 0
    return {region: cents_to_amount(value) for region, value in totals.items()}


@dataclass(frozen=True)
class DealershipRevenue:
    code: str
    name: str
    region: str
    revenue: float
    week: list[float]  # last 7 days of revenue, oldest first
    trend: float | None  # percent change, last 3 days vs the 4 before
    profit: ProfitSummary = field(default_factory=ProfitSummary)  # gross profit over the same sales


def _week_trend(week: list[float]) -> float | None:
    earlier, recent = sum(week[:4]), sum(week[4:])
    return percent_change(recent / 3, earlier / 4) if earlier > 0 else None


def top_dealerships(
    transactions: list[Transaction],
    dealerships: list[Dealership],
    today: date,
    limit: int = 6,
    week_transactions: list[Transaction] | None = None,
) -> list[DealershipRevenue]:
    """The `limit` dealerships with the most revenue in `transactions`,
    each with its last-7-days series (ending `today`) for a sparkline.
    Dealerships with no sales are left out - a ranking of zeros says
    nothing.

    The ranking uses `transactions` (the period); the 7-day series uses
    `week_transactions` (default: the same list). Early in a month the
    period holds fewer than 7 days, so the caller passes the sales from
    Period.week_start on, or the earlier days would read as zero."""
    by_code: dict[str, list[Transaction]] = {}
    for transaction in transactions:
        if transaction.dealership_code:
            by_code.setdefault(transaction.dealership_code, []).append(transaction)
    week_by_code: dict[str, list[Transaction]] = {}
    for transaction in transactions if week_transactions is None else week_transactions:
        if transaction.dealership_code:
            week_by_code.setdefault(transaction.dealership_code, []).append(transaction)
    ranked: list[DealershipRevenue] = []
    for dealership in dealerships:
        sales = by_code.get(dealership.code, [])
        revenue = cents_to_amount(sum(transaction_cents(t) for t in sales))
        if revenue <= 0:
            continue
        week = daily_totals(week_by_code.get(dealership.code, []), today - timedelta(days=6), today)
        ranked.append(
            DealershipRevenue(dealership.code, dealership.name, dealership.region, revenue, week, _week_trend(week),
                              profit_summary(sales))
        )
    ranked.sort(key=lambda d: (-d.revenue, d.name))
    return ranked[:limit]


def sales_today(transactions: list[Transaction], today: date) -> tuple[int, float]:
    """(number of sales, revenue) for `today` out of `transactions`."""
    todays = [t for t in transactions if t.created_at is not None and t.created_at.date() == today]
    return len(todays), cents_to_amount(sum(transaction_cents(t) for t in todays))


# --- The exportable report ------------------------------------------------


def build_period_report(
    transactions: list[Transaction],
    period: Period,
    dealerships: list[Dealership],
    previous: list[Transaction] | None = None,
    week_transactions: list[Transaction] | None = None,
) -> ReportDocument:
    """The Reports page's export (PDF / Excel / CSV all render this one
    document): totals with the comparison, revenue per day, revenue per
    region, the dealership ranking, then the per-product breakdown.
    `week_transactions` are the sales from Period.week_start on, for the
    ranking's 7-day trend (see top_dealerships)."""
    base = build_sales_report(transactions, period.start_datetime, period.end_exclusive)
    base.title = f"Revenue report · {period.start:%Y-%m-%d} to {period.end:%Y-%m-%d} ({period.label})"
    revenue = cents_to_amount(sum(transaction_cents(t) for t in transactions))

    totals_rows: list[tuple[str, ...]] = [("Sales", count_text(len(transactions))), ("Revenue", amount_text(revenue))]
    totals_rows += profit_totals_rows(profit_summary(transactions))
    if previous is not None:
        _, prior, change = period_comparison(period, transactions, previous)
        totals_rows += [
            (f"Previous period ({period.prev_start:%Y-%m-%d} to {period.prev_end:%Y-%m-%d})", amount_text(prior)),
            ("Change", change_cell(change)),
        ]
        if period.is_clamped:
            totals_rows.append((
                "Change compares",
                f"{period.start:%Y-%m-%d} to {period.comparable_end:%Y-%m-%d} against the same {period.comparable_days} days",
            ))
    projection = project_total(revenue, period.days, period.total_days) if period.end < _period_close(period) else None
    if projection is not None:
        totals_rows.append(("Projected for the full period", amount_text(projection)))

    series = daily_totals(transactions, period.start, period.end)
    daily_rows = [(f"{period.start + timedelta(days=i):%Y-%m-%d}", amount_text(value)) for i, value in enumerate(series)]

    region_rows = [(region, amount_text(value)) for region, value in revenue_by_region(transactions, dealerships).items()]
    ranking = top_dealerships(transactions, dealerships, period.end, limit=10, week_transactions=week_transactions)
    with_profit = any(d.profit.has_profit for d in ranking)
    top_rows = []
    for rank, d in enumerate(ranking, start=1):
        row = (count_text(rank), d.name, d.region, amount_text(d.revenue), change_cell(d.trend))
        if with_profit:
            row += (profit_cell(d.profit), margin_cell(d.profit.margin))
        top_rows.append(row)
    top_title = "Top dealerships (rank, name, region, revenue, 7-day trend" + (
        ", gross profit, margin %)" if with_profit else ")")

    sections = [
        ReportSection("Totals", totals_rows),
        ReportSection("Revenue per day", daily_rows),
        ReportSection("Revenue by region", region_rows),
        ReportSection(top_title, top_rows),
    ]
    sections.extend(section for section in base.sections if section.title.startswith(PRODUCT_SECTION_TITLE))
    base.sections = sections
    return base


def _period_close(period: Period) -> date:
    """The last day of the month / quarter / year the period belongs to."""
    return period.start + timedelta(days=period.total_days - 1)


def compact_amount(value: float) -> str:
    """Short axis/label text: "850", "12.3k", "4.82M" (no currency symbol,
    like every other amount in the app)."""
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return localize_number(f"{value / 1_000_000:.2f}M")
    if magnitude >= 10_000:
        return f"{value / 1_000:.0f}k"
    if magnitude >= 1_000:
        return localize_number(f"{value / 1_000:.1f}k")
    return f"{value:.0f}"


def axis_ticks(maximum: float, count: int = 4) -> tuple[float, list[float]]:
    """A round upper bound >= `maximum` and `count + 1` evenly spaced tick
    values from 0 up to it, so a chart's axis ends on a clean number."""
    if maximum <= 0:
        return 1.0, [i / count for i in range(count + 1)]
    import math

    raw_step = maximum / count
    exponent = math.floor(math.log10(raw_step))
    fraction = raw_step / (10 ** exponent)
    nice = next(f for f in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if fraction <= f)
    step = nice * (10 ** exponent)
    top = step * count
    return top, [step * i for i in range(count + 1)]


# --- Everything the Reports page draws, computed in one place -------------


@dataclass
class ReportView:
    period: Period
    mode: str  # "cumulative" | "daily"
    revenue: float
    previous_revenue: float  # over the compared days only (see Period)
    change: float | None
    projected_total: float | None  # None for a closed period or before any sales
    current: list[float]
    previous: list[float]
    projection: list[float]  # continues right after `current`
    total_points: int
    x_labels: list[tuple[int, str]]
    regions: list[tuple[str, float, float]]  # (region, revenue, share of total in %)
    top: list[DealershipRevenue]
    sale_count: int
    profit: ProfitSummary = field(default_factory=ProfitSummary)  # gross profit over the whole window

    def day_label(self, index: int) -> str:
        return long_date_text(self.period.start + timedelta(days=index))

    def readout(self, index: int) -> tuple[str, float | None, float | None, bool]:
        """(date text, this period's value, previous period's value,
        is_projected) for the chart point under the pointer; a value is
        None where that series has no data for that day."""
        current = self.current[index] if 0 <= index < len(self.current) else None
        projected = False
        if current is None and 0 <= index - len(self.current) < len(self.projection):
            current, projected = self.projection[index - len(self.current)], True
        previous = self.previous[index] if 0 <= index < len(self.previous) else None
        return self.day_label(index), current, previous, projected


def build_report_view(
    period: Period,
    transactions: list[Transaction],
    previous: list[Transaction],
    dealerships: list[Dealership],
    mode: str = "cumulative",
    week_transactions: list[Transaction] | None = None,
) -> ReportView:
    if mode not in ("cumulative", "daily"):
        raise ValueError(f"mode must be 'cumulative' or 'daily', got {mode!r}")
    daily = daily_totals(transactions, period.start, period.end)
    prior_daily = daily_totals(previous, period.prev_start, period.prev_end)
    revenue = revenue_between(transactions, period.start, period.end)
    _, previous_revenue, change = period_comparison(period, transactions, previous)

    open_period = period.end < _period_close(period)
    projected_total = project_total(revenue, period.days, period.total_days) if open_period else None
    remaining = period.total_days - period.days if open_period else 0
    average = revenue / period.days if period.days else 0.0
    if projected_total is None or remaining <= 0:
        projection: list[float] = []
    elif mode == "cumulative":
        base = cumulative(daily)[-1]
        projection = [round(base + average * (i + 1), 2) for i in range(remaining)]
    else:
        projection = [round(average, 2)] * remaining

    current = cumulative(daily) if mode == "cumulative" else daily
    prior_series = cumulative(prior_daily) if mode == "cumulative" else prior_daily
    total_points = period.total_days if open_period else period.days

    step = max(1, total_points // 6)
    labels = [(i, day_month_text(period.start + timedelta(days=i))) for i in range(0, total_points, step)]
    if labels and labels[-1][0] != total_points - 1 and total_points - 1 - labels[-1][0] >= step // 2 + 1:
        labels.append((total_points - 1, day_month_text(period.start + timedelta(days=total_points - 1))))

    by_region = revenue_by_region(transactions, dealerships)
    shown = [(region, value) for region, value in by_region.items() if value > 0]
    shown.sort(key=lambda pair: -pair[1])
    total = sum(value for _, value in shown)
    regions = [(region, value, round(value / total * 100, 1) if total else 0.0) for region, value in shown]

    return ReportView(
        period=period, mode=mode, revenue=revenue, previous_revenue=previous_revenue,
        change=change, projected_total=projected_total,
        current=current, previous=prior_series, projection=projection, total_points=total_points,
        x_labels=labels, regions=regions, top=top_dealerships(transactions, dealerships, period.end, week_transactions=week_transactions),
        sale_count=len(transactions), profit=profit_between(transactions, period.start, period.end),
    )
