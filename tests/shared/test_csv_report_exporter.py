"""admin_app.export.csv_report_exporter."""

import csv
import tempfile
from datetime import datetime
from pathlib import Path

from admin_app.export.csv_report_exporter import export_to_csv
from shared.builders.report_builder import ReportDocument, ReportSection


def test_csv_has_letterhead_sections_and_survives_accents_and_commas():
    report = ReportDocument(
        title="Revenue report",
        generated_at=datetime(2026, 9, 24, 14, 30),
        sections=[ReportSection("Totals", [("Sales", "2"), ("Revenue", "1,150.00")]),
                  ReportSection("Top", [("1", "Şişli, Merkez", "Metro", "100.00")])],
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "out.csv"
        export_to_csv(report, path)
        raw = path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # BOM, so Excel reads UTF-8
        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[1] == ["Revenue report"] and rows[2] == ["Generated 2026-09-24 14:30"]
    assert ["Totals"] in rows and ["Sales", "2"] in rows and ["Revenue", "1,150.00"] in rows
    assert ["1", "Şişli, Merkez", "Metro", "100.00"] in rows
