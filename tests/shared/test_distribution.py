"""Tests for shared.distribution - live status, progress, KPIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.distribution import (
    duration_text,
    eta_text,
    lateness,
    live_status,
    progress,
    summarize,
)
from shared.formatting import to_db_timestamp
from shared.models import Shipment, ShipmentLine

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _s(status="in_transit", departed=-2.0, eta=2.0, planned=None, delivered=None, items=10, origin="WH-01"):
    """Offsets in hours from NOW."""
    at = lambda h: to_db_timestamp(NOW + timedelta(hours=h))  # noqa: E731
    return Shipment(
        origin=origin,
        dealership_code="001",
        dealership_name="Harbor Point",
        carrier="Ridgeline",
        planned_eta=at(planned if planned is not None else eta),
        eta=at(eta),
        departed_at=at(departed) if departed is not None and status != "scheduled" else None,
        delivered_at=at(delivered) if delivered is not None else None,
        status=status,
        lines=[ShipmentLine("A", "A", items)],
        id=1,
    )


@pytest.mark.parametrize(
    "shipment, expected",
    [
        (_s(eta=3), "In Transit"),
        (_s(eta=0.5), "Arriving"),
        (_s(eta=5, planned=2), "Delayed"),  # ETA pushed back 3h
        (_s(eta=-1), "Delayed"),  # ETA passed, still not received
        (_s(eta=0.1, planned=0), "Arriving"),  # 6 min later than promised: under threshold
        (_s(status="scheduled", eta=6), "Scheduled"),
        (_s(status="delivered", delivered=-1), "Delivered"),
        (_s(status="cancelled"), "Cancelled"),
    ],
)
def test_live_status(shipment, expected):
    assert live_status(shipment, NOW) == expected


def test_lateness():
    assert lateness(_s(eta=5, planned=2), NOW) == timedelta(hours=3)
    assert lateness(_s(eta=-1, planned=-1), NOW) == timedelta(hours=1)  # overdue by an hour so far
    assert lateness(_s(eta=3), NOW) == timedelta(0)
    assert lateness(_s(status="delivered", planned=-2, delivered=-1), NOW) == timedelta(hours=1)


def test_progress_is_estimated_from_the_schedule():
    assert progress(_s(departed=-2, eta=2), NOW) == pytest.approx(0.5)
    assert progress(_s(departed=-1, eta=-0.5), NOW) == 0.99  # overdue: never "done" until received
    assert progress(_s(status="scheduled"), NOW) is None
    assert progress(_s(status="delivered", delivered=-1), NOW) == 1.0


@pytest.mark.parametrize(
    "delta, text",
    [(timedelta(hours=2, minutes=50), "+2h 50m"), (timedelta(hours=3), "+3h"), (timedelta(minutes=45), "+45m")],
)
def test_duration_text(delta, text):
    assert duration_text(delta) == text


def test_eta_text_mentions_arrival_once_delivered():
    assert eta_text(_s(status="delivered", delivered=-1), NOW).startswith("Arrived ")
    assert eta_text(_s(eta=24), NOW).startswith("Tomorrow ")


def test_summarize():
    shipments = [
        _s(eta=3, items=184, origin="WH-01"),
        _s(eta=-1, items=96, origin="WH-02"),  # delayed 1h
        _s(eta=5, planned=2, items=10),  # delayed 3h
        _s(status="delivered", delivered=-1, planned=-1),
        _s(status="scheduled", eta=30),  # tomorrow - not due today
    ]
    s = summarize(shipments, NOW)

    assert s.in_transit_shipments == 3
    assert s.in_transit_items == 290
    assert s.origins == {"WH-01", "WH-02"}
    assert s.delivered_today == 1
    assert s.due_today == 4  # 3 on the road today + 1 already delivered
    assert len(s.delayed) == 2
    assert s.average_delay == timedelta(hours=2)
