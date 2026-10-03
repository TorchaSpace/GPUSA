"""What the Distribution screens compute from shipments - pure functions
over shared.models.Shipment, no Qt, no database, shared by admin_app's
Distribution page, depot_app's Shipments page and pos_app's Receive
Inventory screen so all three say the same thing about a shipment.

Nothing here pretends to know where a truck is. There's no GPS or
carrier feed in this system (the admin mockup's "Carrier feeds refresh
every 30 s" and its drifting progress bars are fiction), so:
- progress is ESTIMATED from the schedule: time since departure over the
  departure->ETA window, capped at 99% until someone actually receives
  it;
- "Arriving" means due within ARRIVING_WINDOW of now;
- "Delayed" means the current estimate is at least DELAY_THRESHOLD later
  than what was promised (`planned_eta`) - either because someone moved
  the ETA, or because the ETA has simply passed and it still isn't in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shared.formatting import parse_db_timestamp
from shared.models import Shipment

ARRIVING_WINDOW = timedelta(minutes=60)
DELAY_THRESHOLD = timedelta(minutes=15)

STATUS_LABELS = {
    "scheduled": "Scheduled",
    "in_transit": "In Transit",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def effective_eta(shipment: Shipment, now: datetime) -> datetime:
    """When it will arrive at the earliest: its ETA, or now if the ETA has
    already passed and it still hasn't been received."""
    eta = parse_db_timestamp(shipment.eta)
    if shipment.is_active and now > eta:
        return now
    return eta


def lateness(shipment: Shipment, now: datetime | None = None) -> timedelta:
    """How much later than promised it is (or was): zero if on time."""
    now = now or _utc_now()
    planned = parse_db_timestamp(shipment.planned_eta)
    if shipment.status == "delivered" and shipment.delivered_at:
        actual = parse_db_timestamp(shipment.delivered_at)
    elif shipment.status == "cancelled":
        return timedelta(0)
    else:
        actual = effective_eta(shipment, now)
    return max(actual - planned, timedelta(0))


def live_status(shipment: Shipment, now: datetime | None = None) -> str:
    """"Scheduled" / "In Transit" / "Arriving" / "Delayed" / "Delivered" /
    "Cancelled"."""
    now = now or _utc_now()
    if not shipment.is_active:
        return STATUS_LABELS[shipment.status]
    if lateness(shipment, now) >= DELAY_THRESHOLD:
        return "Delayed"
    if shipment.status == "scheduled":
        return "Scheduled"
    if parse_db_timestamp(shipment.eta) - now <= ARRIVING_WINDOW:
        return "Arriving"
    return "In Transit"


def progress(shipment: Shipment, now: datetime | None = None) -> float | None:
    """Estimated share of the trip done, 0.0-1.0 (see module docstring);
    None before departure. Delivered = 1.0. Never 1.0 before delivery."""
    now = now or _utc_now()
    if shipment.status == "delivered":
        return 1.0
    if shipment.status != "in_transit" or not shipment.departed_at:
        return None
    departed = parse_db_timestamp(shipment.departed_at)
    eta = effective_eta(shipment, now)
    total = (eta - departed).total_seconds()
    if total <= 0:
        return 0.99
    return max(0.0, min((now - departed).total_seconds() / total, 0.99))


def duration_text(delta: timedelta) -> str:
    """"+2h 50m", "+45m", "+3h" - the mockup's late-by note."""
    minutes = int(round(delta.total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"+{hours}h {minutes}m"
    if hours:
        return f"+{hours}h"
    return f"+{minutes}m"


def eta_text(shipment: Shipment, now: datetime | None = None) -> str:
    """"14:55", "Tomorrow 09:30", "Mon 29 Sep 09:30" in local time, or
    "Arrived 11:58" once delivered."""
    now = now or _utc_now()
    if shipment.status == "delivered" and shipment.delivered_at:
        at = parse_db_timestamp(shipment.delivered_at).astimezone()
        return f"Arrived {at.strftime('%H:%M')}"
    at = parse_db_timestamp(shipment.eta).astimezone()
    today = now.astimezone().date()
    if at.date() == today:
        return at.strftime("%H:%M")
    if at.date() == today + timedelta(days=1):
        return f"Tomorrow {at.strftime('%H:%M')}"
    if at.date() == today - timedelta(days=1):
        return f"Yesterday {at.strftime('%H:%M')}"
    return at.strftime("%a %d %b %H:%M")


@dataclass
class DistributionSummary:
    in_transit_items: int = 0
    in_transit_shipments: int = 0
    origins: set[str] = field(default_factory=set)
    delivered_today: int = 0
    due_today: int = 0  # delivered today + still expected today
    delayed: list[Shipment] = field(default_factory=list)
    average_delay: timedelta | None = None  # None when nothing is delayed


def summarize(shipments: list[Shipment], now: datetime | None = None) -> DistributionSummary:
    """The admin mockup's three KPI cards: items/shipments on the road and
    from how many origins; deliveries completed today out of everything
    due today; delayed shipments and their average lateness."""
    now = now or _utc_now()
    today = now.astimezone().date()
    summary = DistributionSummary()
    for shipment in shipments:
        if shipment.status == "in_transit":
            summary.in_transit_shipments += 1
            summary.in_transit_items += shipment.item_count
            summary.origins.add(shipment.origin)
        if shipment.status == "delivered" and shipment.delivered_at:
            if parse_db_timestamp(shipment.delivered_at).astimezone().date() == today:
                summary.delivered_today += 1
                summary.due_today += 1
        elif shipment.is_active and effective_eta(shipment, now).astimezone().date() == today:
            summary.due_today += 1
        if shipment.is_active and live_status(shipment, now) == "Delayed":
            summary.delayed.append(shipment)
    if summary.delayed:
        total = sum((lateness(s, now) for s in summary.delayed), timedelta(0))
        summary.average_delay = total / len(summary.delayed)
    return summary
