"""shared.warehousing - capacity status, employee matching, log text."""

from datetime import date

from shared.models import Warehouse
from shared.warehousing import (
    capacity_fraction,
    capacity_status,
    direction_label,
    percent_text,
    reason_label,
    reference_text,
    start_of_today_db,
    works_at,
)

WH = Warehouse(code="WH-01", name="İstanbul Merkez", capacity_units=1000)


def test_capacity_status_and_threshold():
    assert capacity_fraction(849, 1000) == 0.849 and capacity_fraction(5, None) is None
    assert capacity_status(WH, 849) == "Operational"
    assert capacity_status(WH, 850) == "Near capacity"
    assert capacity_status(WH, 1200) == "Near capacity" and percent_text(1.2) == "120%"
    assert capacity_status(Warehouse(code="X", name="X"), 10) == "Capacity not set"
    assert capacity_status(Warehouse(code="X", name="X", capacity_units=5, is_active=False), 1) == "Inactive"
    assert percent_text(None) == "—"


def test_employee_matching_is_loose_about_case_and_turkish_letters():
    for typed in ("İstanbul Merkez", "istanbul merkez", "ISTANBUL  MERKEZ", " wh-01 ", "WH-01 · İstanbul Merkez"):
        assert works_at("Warehouse", typed, WH), typed
    assert not works_at("Dealership", "WH-01", WH)
    assert not works_at("Warehouse", "", WH)
    assert not works_at("Warehouse", "Ankara", WH)


def test_log_text():
    transfer_in = {"movement_type": "receive", "reason": "transfer", "reference": "Unassigned", "note": None}
    assert (direction_label(transfer_in), reason_label(transfer_in), reference_text(transfer_in)) == (
        "Check-in", "Transfer", "from Unassigned")
    loaded = {"movement_type": "dispatch", "reason": "shipment", "reference": "SH-00004", "note": "Loaded for X"}
    assert (direction_label(loaded), reference_text(loaded)) == ("Check-out", "SH-00004 · Loaded for X")
    legacy = {"movement_type": "receive", "reason": None, "reference": None, "note": None}
    assert (reason_label(legacy), reference_text(legacy)) == ("Floor log", "—")


def test_start_of_today_is_a_db_timestamp():
    text = start_of_today_db(date(2026, 9, 26))
    assert text.endswith("Z") and "T" in text
