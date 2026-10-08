"""The depot's movement Excel export must not carry live formulas from typed names."""

from __future__ import annotations

from types import SimpleNamespace

from openpyxl import load_workbook

from depot_app.export.movement_report_export import export_excel


def test_names_that_look_like_formulas_are_defused(tmp_path):
    report = SimpleNamespace(
        title="Movements", period_text="today", kinds_present=[],
        lines=[SimpleNamespace(barcode="A1", name="=HYPERLINK(\"http://x\")", units_in=1, units_out=0, net=1, by_kind={})],
        units_in=1, units_out=0, net=1, kind_total=lambda key: 0,
        movements=[{"created_at": "2026-10-08T10:00:00Z", "movement_type": "receive", "barcode": "A1",
                    "product_name": "@SUM(1)", "quantity": 1, "reason": "receive", "reference": None,
                    "handled_by": "+cmd"}],
    )
    path = tmp_path / "m.xlsx"
    export_excel(report, path)
    book = load_workbook(path)
    summary, detail = book.worksheets
    assert str(summary["B5"].value).startswith("'=")
    values = [c.value for c in detail[2]]
    assert "'@SUM(1)" in values and "'+cmd" in values
