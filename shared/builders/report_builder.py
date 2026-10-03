"""Pure function: a list of Transactions in, a sales report document out.

Same builder feeds the on-screen report preview in admin_app AND both
admin_app/export/pdf_report_exporter.py and excel_report_exporter.py -
one builder, parameterized, never duplicated per export format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shared.models import Transaction


@dataclass
class ReportSection:
    title: str
    rows: list[tuple[str, ...]]  # generic tabular rows; exporters decide column widths/styling


@dataclass
class ReportDocument:
    title: str
    generated_at: datetime
    sections: list[ReportSection] = field(default_factory=list)


def build_sales_report(transactions: list[Transaction], range_start: datetime, range_end: datetime) -> ReportDocument:
    """Build a ReportDocument summarizing `transactions` in [range_start, range_end).

    Two sections, both plain ReportSection rows so pdf_report_exporter
    and excel_report_exporter can each lay them out in their own format
    without touching this aggregation logic:
      - Totals: sale count and total revenue.
      - Per-Product Breakdown: units sold and revenue, one row per
        product that appears in at least one line item, sorted by name.
    """
    sale_count = len(transactions)
    revenue = round(sum(transaction.total for transaction in transactions), 2)
    totals_section = ReportSection(
        title="Totals",
        rows=[
            ("Sales", str(sale_count)),
            ("Revenue", f"{revenue:.2f}"),
        ],
    )

    # barcode -> {"name": ..., "units": ..., "revenue": ...}
    per_product: dict[str, dict] = {}
    for transaction in transactions:
        for item in transaction.items:
            entry = per_product.setdefault(
                item.product_barcode,
                {"name": item.product_name_at_sale, "units": 0, "revenue": 0.0},
            )
            entry["units"] += item.quantity
            entry["revenue"] += item.line_total

    breakdown_rows = [
        (barcode, entry["name"], str(entry["units"]), f"{entry['revenue']:.2f}")
        for barcode, entry in sorted(per_product.items(), key=lambda pair: pair[1]["name"])
    ]
    breakdown_section = ReportSection(title="Per-Product Breakdown", rows=breakdown_rows)

    return ReportDocument(
        title=f"Sales Report: {range_start:%Y-%m-%d} to {range_end:%Y-%m-%d}",
        generated_at=datetime.now(),
        sections=[totals_section, breakdown_section],
    )
