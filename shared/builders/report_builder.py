"""Pure function: a list of Transactions in, a sales report document out.

Same builder feeds the on-screen report preview in admin_app AND both
admin_app/export/pdf_report_exporter.py and excel_report_exporter.py -
one builder, parameterized, never duplicated per export format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from shared.models import Transaction


_CENT = Decimal("0.01")


def line_cents(item) -> int:
    """A line item's total in whole cents, price x quantity rounded
    half-up on the DECIMAL value of the price (so a 3-decimal price like
    1.005 x 1 is 1.01, not the 1.00 binary-float rounding gives)."""
    amount = Decimal(str(item.unit_price_at_sale)) * Decimal(str(item.quantity))
    return int((amount.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def transaction_cents(transaction: Transaction) -> int:
    return sum(line_cents(item) for item in transaction.items)


def cents_to_amount(cents: int) -> float:
    return cents / 100


class NumericText(str):
    """Text that is really a number. Everywhere a report is shown as text
    (preview table, PDF, CSV) it behaves as the plain string it prints as
    ("150.00"); the Excel exporter instead writes `number` as a real
    numeric cell with `number_format`, so sums and sorting work there."""

    number: int | float
    number_format: str

    def __new__(cls, text: str, number: int | float, number_format: str = "General"):
        obj = super().__new__(cls, text)
        obj.number = number
        obj.number_format = number_format
        return obj


def count_text(value: int) -> NumericText:
    return NumericText(str(value), value, "#,##0")


def amount_text(value: float) -> NumericText:
    return NumericText(f"{value:.2f}", value, "#,##0.00")


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
    revenue = cents_to_amount(sum(transaction_cents(t) for t in transactions))
    totals_section = ReportSection(
        title="Totals",
        rows=[
            ("Sales", count_text(sale_count)),
            ("Revenue", amount_text(revenue)),
        ],
    )

    # barcode -> {"name": ..., "units": ..., "revenue": ...}
    per_product: dict[str, dict] = {}
    for transaction in transactions:
        for item in transaction.items:
            entry = per_product.setdefault(
                item.product_barcode,
                {"name": item.product_name_at_sale, "units": 0, "revenue": 0},
            )
            entry["units"] += item.quantity
            entry["revenue"] += line_cents(item)

    breakdown_rows = [
        (barcode, entry["name"], count_text(entry["units"]), amount_text(cents_to_amount(entry["revenue"])))
        for barcode, entry in sorted(per_product.items(), key=lambda pair: pair[1]["name"])
    ]
    breakdown_section = ReportSection(title="Per-Product Breakdown", rows=breakdown_rows)

    # range_end is exclusive (start of the day AFTER the last one); the
    # title names the last day actually covered.
    last_day = max(range_start, range_end - timedelta(microseconds=1))
    return ReportDocument(
        title=f"Sales Report: {range_start:%Y-%m-%d} to {last_day:%Y-%m-%d}",
        generated_at=datetime.now(),
        sections=[totals_section, breakdown_section],
    )
