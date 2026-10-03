"""shared.warehousing - capacity status, employee matching, log text."""

from datetime import date

import pytest

from shared.models import Warehouse
from shared.warehousing import (
    capacity_fraction,
    capacity_status,
    clean_price,
    direction_label,
    normalise_barcode,
    normalise_code,
    percent_text,
    percent_used,
    tr_or,
    whole_number,
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


def test_percent_is_floored_so_it_never_contradicts_the_status():
    wh = Warehouse(code="WH-01", name="x", capacity_units=1000)
    # 99.6% is not "100%": that reads full while the pill still says Operational/Near capacity.
    assert percent_text(capacity_fraction(996, 1000)) == "99%"
    # 84.9% never rounds up to the 85% that flips the status.
    assert percent_text(capacity_fraction(849, 1000)) == "84%" and capacity_status(wh, 849) == "Operational"
    assert percent_text(capacity_fraction(850, 1000)) == "85%" and capacity_status(wh, 850) == "Near capacity"
    assert percent_text(capacity_fraction(1000, 1000)) == "100%"
    assert percent_text(capacity_fraction(1001, 1000)) == "100%"  # over by one unit: still floors to 100, text can't say 99
    assert percent_text(capacity_fraction(1119, 1000)) == "111%"
    assert percent_used(0.29) == 29  # float noise: 0.29 * 100 = 28.999999999999996
    assert percent_text(0) == "0%"


def test_status_and_percent_agree_for_every_unit_of_a_small_warehouse():
    wh = Warehouse(code="WH-01", name="x", capacity_units=7)
    for used in range(0, 12):
        pct = int(percent_text(capacity_fraction(used, 7)).rstrip("%"))
        assert (capacity_status(wh, used) == "Near capacity") == (pct >= 85), (used, pct)


@pytest.mark.parametrize("value, expected", [(3, 3), (3.0, 3), ("4", 4), (" 5 ", 5), (0, 0), (-2, -2)])
def test_whole_number_accepts_whole_values(value, expected):
    assert whole_number(value) == expected


@pytest.mark.parametrize("value", [2.7, 0.1, float("nan"), float("inf"), "3.5", "", "abc", None, True, [1], 1e300])
def test_whole_number_refuses_everything_else(value):
    with pytest.raises(ValueError):
        whole_number(value, "Quantity")


@pytest.mark.parametrize("value, expected", [(19.999, 20.0), (0.125, 0.13), (0, 0.0), ("5.5", 5.5), (1, 1.0), (2.675, 2.68)])
def test_clean_price_rounds_half_up_to_two_decimals(value, expected):
    assert clean_price(value) == expected


@pytest.mark.parametrize("value", [-0.01, float("nan"), float("inf"), "abc", None, True, "", 1e300])
def test_clean_price_refuses_bad_values(value):
    with pytest.raises(ValueError):
        clean_price(value)


def test_normalising_codes_and_barcodes():
    assert normalise_barcode("  abc-1 ") == "ABC-1" and normalise_code(" wh-01 ") == "WH-01"
    for blank in ("", "   ", None):
        with pytest.raises(ValueError):
            normalise_barcode(blank)
        with pytest.raises(ValueError):
            normalise_code(blank)


def test_tr_or_falls_back_to_english_while_a_key_is_missing():
    assert tr_or("no.such.key.yet", "English text") == "English text"
    assert tr_or("common.save", "ignored") == "Save"
