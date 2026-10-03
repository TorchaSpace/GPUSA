"""Renders a built ReportDocument to a .csv file. Mirrors pdf_report_exporter.py
and excel_report_exporter.py - same document, plain-text format.

Written as UTF-8 with a byte-order mark so Excel on both Windows and macOS
opens names with accents correctly instead of guessing a legacy codepage.
"""

from __future__ import annotations

import csv
from pathlib import Path

from shared.builders.report_builder import ReportDocument
from shared.constants import STORE_NAME


def export_to_csv(report: ReportDocument, output_path: Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([STORE_NAME])
        writer.writerow([report.title])
        writer.writerow([f"Generated {report.generated_at:%Y-%m-%d %H:%M}"])
        for section in report.sections:
            writer.writerow([])
            writer.writerow([section.title])
            for row in section.rows:
                writer.writerow(list(row))
