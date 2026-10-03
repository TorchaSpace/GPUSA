"""shared.builders.movement_report_builder."""

from datetime import date

from shared.builders.movement_report_builder import build_movement_report, kind_of


def _m(barcode, name, mtype, qty, reason):
    return {"barcode": barcode, "product_name": name, "movement_type": mtype, "quantity": qty, "reason": reason,
            "created_at": "2026-09-26T08:00:00.000Z", "note": None, "reference": None, "handled_by": None}


def test_groups_per_product_and_kind():
    rows = [
        _m("BOX", "Carton", "receive", 100, "receive"),
        _m("BOX", "Carton", "dispatch", 20, "shipment"),
        _m("BOX", "Carton", "receive", 20, "shipment"),  # cancelled shipment came back
        _m("BOX", "Carton", "dispatch", 3, "count"),
        _m("TAPE", "Tape", "receive", 5, "transfer"),
        _m("TAPE", "Tape", "dispatch", 1, None),  # legacy row
    ]
    report = build_movement_report(rows, "WH-01 · Test", date(2026, 9, 20), date(2026, 9, 26))
    box, tape = report.lines
    assert (box.barcode, box.units_in, box.units_out, box.net, box.movements) == ("BOX", 120, 23, 97, 4)
    assert box.by_kind == {"receipts": 100, "shipped": 20, "returned": 20, "count_down": 3}
    assert tape.by_kind == {"transfer_in": 5, "other_out": 1}
    assert (report.units_in, report.units_out, report.net) == (125, 24, 101)
    assert [key for key, _ in report.kinds_present] == ["receipts", "shipped", "returned", "transfer_in", "count_down",
                                                         "other_out"]
    assert report.period_text == "20.09.2026 – 26.09.2026"
    assert report.title == "Stock movements · WH-01 · Test"


def test_empty_and_single_day():
    report = build_movement_report([], "WH-01", date(2026, 9, 26), date(2026, 9, 26))
    assert report.lines == [] and report.net == 0 and report.kinds_present == []
    assert report.period_text == "26.09.2026"


def test_kind_of():
    assert kind_of({"movement_type": "dispatch", "reason": "dispatch"}) == "written_out"
    assert kind_of({"movement_type": "receive", "reason": "discrepancy"}) == "other_in"
