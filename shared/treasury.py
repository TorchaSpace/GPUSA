"""Everything Treasury & Ledger screens compute from ledger entries -
pure functions over shared.models.LedgerEntry, no Qt, no database, so
admin_app and depot_app show identical numbers and it's all testable.

"Overdue" lives here, not in the database: an entry is overdue when it's
still pending and its due date is before today. Storing it (as the
mockups' data does) would make every row wrong the morning after.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from shared.models import LedgerEntry

DUE_SOON_DAYS = 7
MILESTONE_DAYS = 30


def is_overdue(entry: LedgerEntry, today: date) -> bool:
    return entry.is_open and entry.due_date < today


def days_until_due(entry: LedgerEntry, today: date) -> int:
    return (entry.due_date - today).days


def display_status(entry: LedgerEntry, today: date) -> str:
    """"Overdue" / "Pending" / "Open" / "Cleared" / "Endorsed".
    An incoming invoice that's still unpaid reads "Open" (the depot
    mockup's word for an open receivable); every other pending
    document reads "Pending"."""
    if entry.status == "cleared":
        return "Cleared"
    if entry.status == "endorsed":
        return "Endorsed"
    if is_overdue(entry, today):
        return "Overdue"
    if entry.direction == "in" and entry.doc_type == "invoice":
        return "Open"
    return "Pending"


def due_relative_text(entry: LedgerEntry, today: date) -> str:
    """"Settled", "Today", "in 1 day", "in 5 days", "3 days overdue"."""
    if not entry.is_open:
        return "Settled"
    days = days_until_due(entry, today)
    if days == 0:
        return "Today"
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    return f"in {days} day{'s' if days != 1 else ''}"


def matches_status_filter(entry: LedgerEntry, status_filter: str, today: date) -> bool:
    """The admin mockup's All / Pending / Cleared / Overdue filter.
    "Pending" means pending and not yet overdue (the two are shown as
    different statuses); "Cleared" includes endorsed (both are settled)."""
    if status_filter == "All":
        return True
    status = display_status(entry, today)
    if status_filter == "Cleared":
        return status in ("Cleared", "Endorsed")
    if status_filter == "Pending":
        return status in ("Pending", "Open")
    return status == status_filter


@dataclass
class TreasurySummary:
    receivables_total: float = 0.0
    receivables_checks: float = 0.0
    receivables_notes: float = 0.0
    receivables_overdue: float = 0.0
    payables_total: float = 0.0
    payables_checks: float = 0.0
    payables_transfers: float = 0.0  # everything outgoing that isn't a check (mockup's "Transfers")
    due_soon: list[LedgerEntry] = field(default_factory=list)

    @property
    def net_position(self) -> float:
        return round(self.receivables_total - self.payables_total, 2)

    @property
    def due_soon_amount(self) -> float:
        return round(sum(e.amount for e in self.due_soon), 2)


def summarize(entries: list[LedgerEntry], today: date) -> TreasurySummary:
    """The admin mockup's three KPI cards. Only open (pending) documents
    count toward receivables/payables; "due soon" is open documents due
    today through the next DUE_SOON_DAYS days, soonest first (overdue
    ones are counted under Overdue instead, as in the mockup)."""
    summary = TreasurySummary()
    for entry in entries:
        if not entry.is_open:
            continue
        if entry.direction == "in":
            summary.receivables_total += entry.amount
            if entry.doc_type == "check":
                summary.receivables_checks += entry.amount
            elif entry.doc_type == "note":
                summary.receivables_notes += entry.amount
            if is_overdue(entry, today):
                summary.receivables_overdue += entry.amount
        else:
            summary.payables_total += entry.amount
            if entry.doc_type == "check":
                summary.payables_checks += entry.amount
            else:
                summary.payables_transfers += entry.amount
        if 0 <= days_until_due(entry, today) <= DUE_SOON_DAYS:
            summary.due_soon.append(entry)
    summary.due_soon.sort(key=lambda e: (e.due_date, e.id or 0))
    for name in ("receivables_total", "receivables_checks", "receivables_notes", "receivables_overdue",
                 "payables_total", "payables_checks", "payables_transfers"):
        setattr(summary, name, round(getattr(summary, name), 2))
    return summary


@dataclass
class DayMilestones:
    day: date
    incoming: list[LedgerEntry]
    outgoing: list[LedgerEntry]
    cumulative_net: float  # running net of scheduled items up to and including this day


def milestones(entries: list[LedgerEntry], today: date, days: int = MILESTONE_DAYS) -> list[DayMilestones]:
    """The admin mockup's "Next 30 days · Payment & collection milestones"
    strip: open documents grouped by due date for `days` days from today.
    Already-overdue open documents are shown on today (they're due now),
    as the mockup does.

    `cumulative_net` is the running total of scheduled collections minus
    payments. The mockup draws a "projected balance" line starting from a
    made-up cash balance; no cash/bank balance is recorded anywhere in
    this system, so this starts from zero and is labelled as the net of
    scheduled items instead - the shape of the line is the same, the
    starting point is honest.
    """
    open_entries = [e for e in entries if e.is_open]
    result: list[DayMilestones] = []
    running = 0.0
    for offset in range(days):
        day = today + timedelta(days=offset)
        if offset == 0:
            on_day = [e for e in open_entries if e.due_date <= today]
        else:
            on_day = [e for e in open_entries if e.due_date == day]
        incoming = [e for e in on_day if e.direction == "in"]
        outgoing = [e for e in on_day if e.direction == "out"]
        running += sum(e.amount for e in incoming) - sum(e.amount for e in outgoing)
        result.append(DayMilestones(day, incoming, outgoing, round(running, 2)))
    return result


def compact_amount(value: float) -> str:
    """The mockups' compact money style ("212k", "1.84M") without a
    currency symbol, which the app doesn't invent anywhere else either."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{sign}{value / 1_000:.1f}k".replace(".0k", "k")
    return f"{sign}{value:,.0f}"
