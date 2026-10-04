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


def line_cost_cents(item) -> int:
    """What a line's units cost us, in whole cents (unit cost at sale x
    quantity, half-up on the decimal value). Only meaningful when
    `item.cost_known`."""
    amount = Decimal(str(getattr(item, "unit_cost_at_sale", 0) or 0)) * Decimal(str(item.quantity))
    return int((amount.quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


@dataclass(frozen=True)
class ProfitSummary:
    """Revenue, cost and gross profit of a set of sales, all in whole cents.

    Profit only counts lines whose cost was known when they were sold
    (`cost_known`): a line with no cost on record would otherwise look like
    100% margin. So `profit_cents` = revenue of the known-cost lines minus
    their cost, `margin` = that profit over the SAME known-cost revenue, and
    the revenue of the other lines is reported as `unknown_revenue_cents` so
    every screen and export can say how much of the picture is missing."""

    revenue_cents: int = 0  # every line
    known_revenue_cents: int = 0  # lines with a known cost
    cost_cents: int = 0  # cost of those lines
    known_lines: int = 0  # how many lines that is

    @property
    def profit_cents(self) -> int:
        return self.known_revenue_cents - self.cost_cents

    @property
    def unknown_revenue_cents(self) -> int:
        return self.revenue_cents - self.known_revenue_cents

    @property
    def has_profit(self) -> bool:
        """At least one sold line has a known cost, so a profit figure exists."""
        return self.known_lines > 0

    @property
    def margin(self) -> float | None:
        """Gross profit as a percentage of the known-cost revenue; None when
        there is none (never a division by zero)."""
        if self.known_revenue_cents <= 0:
            return None
        return self.profit_cents * 100 / self.known_revenue_cents

    @property
    def is_partial(self) -> bool:
        """Some, but not all, revenue has a known cost."""
        return 0 < self.unknown_revenue_cents and self.has_profit

    @property
    def unknown_percent(self) -> int:
        """Share of revenue with unknown cost as a whole percent, never
        shown as 0 while some is unknown nor 100 while some is known; 0 for
        no revenue."""
        if self.revenue_cents <= 0 or self.unknown_revenue_cents <= 0:
            return 0
        percent = round(self.unknown_revenue_cents * 100 / self.revenue_cents)
        return min(max(percent, 1), 99) if self.has_profit else 100

    @property
    def profit(self) -> float:
        return cents_to_amount(self.profit_cents)

    def __add__(self, other: "ProfitSummary") -> "ProfitSummary":
        return ProfitSummary(self.revenue_cents + other.revenue_cents,
                             self.known_revenue_cents + other.known_revenue_cents,
                             self.cost_cents + other.cost_cents, self.known_lines + other.known_lines)


def profit_summary(transactions) -> ProfitSummary:
    """Fold the lines of `transactions` into a ProfitSummary."""
    revenue = known = cost = lines = 0
    for transaction in transactions:
        for item in transaction.items:
            cents = line_cents(item)
            revenue += cents
            if getattr(item, "cost_known", False):
                known += cents
                cost += line_cost_cents(item)
                lines += 1
    return ProfitSummary(revenue, known, cost, lines)


def margin_text(margin: float | None) -> str:
    """"32.5%" or "n/a" - the plain-text margin cell of an export."""
    return "n/a" if margin is None else f"{margin:.1f}%"


def margin_cell(margin: float | None):
    """The export cell for a margin: a percentage-formatted number in Excel
    ("32.5%" as text elsewhere), "n/a" without one."""
    if margin is None:
        return "n/a"
    return NumericText(f"{margin:.1f}%", round(margin, 1) / 100, "0.0%")


def profit_cell(summary: ProfitSummary):
    return amount_text(summary.profit) if summary.has_profit else "n/a"


def cost_unknown_note(summary: ProfitSummary) -> str:
    return f"Cost unknown for {summary.unknown_percent}% of revenue"


def profit_totals_rows(summary: ProfitSummary) -> list[tuple[str, ...]]:
    """The Totals rows for profit - none when there is no revenue at all
    (nothing to say), else Gross profit and Margin (n/a while no sold
    line has a known cost) and, whenever part of the revenue has no known
    cost, a "Cost unknown for N% of revenue" line."""
    if summary.revenue_cents <= 0:
        return []
    rows: list[tuple[str, ...]] = [
        ("Gross profit", profit_cell(summary)),
        ("Margin", margin_cell(summary.margin)),
    ]
    if summary.unknown_revenue_cents > 0:
        rows.append(("Profit note", cost_unknown_note(summary)))
    return rows


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


PRODUCT_SECTION_TITLE = "Per-Product Breakdown"
PRODUCT_PROFIT_COLUMNS = " (barcode, product, units, revenue, gross profit, margin %)"


def build_sales_report(transactions: list[Transaction], range_start: datetime, range_end: datetime) -> ReportDocument:
    """Build a ReportDocument summarizing `transactions` in [range_start, range_end).

    Two sections, both plain ReportSection rows so pdf_report_exporter
    and excel_report_exporter can each lay them out in their own format
    without touching this aggregation logic:
      - Totals: sale count and total revenue, then gross profit, margin and
        (when some revenue has no known cost) how much of it is unknown.
      - Per-Product Breakdown: units sold and revenue, one row per
        product that appears in at least one line item, sorted by name -
        plus gross profit and margin % columns once any sold line has a
        known cost (n/a for a product with none).
    """
    sale_count = len(transactions)
    revenue = cents_to_amount(sum(transaction_cents(t) for t in transactions))
    totals_section = ReportSection(
        title="Totals",
        rows=[
            ("Sales", count_text(sale_count)),
            ("Revenue", amount_text(revenue)),
            *profit_totals_rows(profit_summary(transactions)),
        ],
    )

    # barcode -> {"name": ..., "units": ..., "revenue": ...}
    per_product: dict[str, dict] = {}
    for transaction in transactions:
        for item in transaction.items:
            entry = per_product.setdefault(
                item.product_barcode,
                {"name": item.product_name_at_sale, "units": 0, "revenue": 0, "profit": ProfitSummary()},
            )
            entry["units"] += item.quantity
            entry["revenue"] += line_cents(item)
            entry["profit"] += profit_summary([Transaction(items=[item])])

    with_profit = any(entry["profit"].has_profit for entry in per_product.values())
    breakdown_rows = []
    for barcode, entry in sorted(per_product.items(), key=lambda pair: pair[1]["name"]):
        row = (barcode, entry["name"], count_text(entry["units"]), amount_text(cents_to_amount(entry["revenue"])))
        if with_profit:
            row += (profit_cell(entry["profit"]), margin_cell(entry["profit"].margin))
        breakdown_rows.append(row)
    breakdown_section = ReportSection(
        title=PRODUCT_SECTION_TITLE + (PRODUCT_PROFIT_COLUMNS if with_profit else ""), rows=breakdown_rows
    )

    # range_end is exclusive (start of the day AFTER the last one); the
    # title names the last day actually covered.
    last_day = max(range_start, range_end - timedelta(microseconds=1))
    return ReportDocument(
        title=f"Sales Report: {range_start:%Y-%m-%d} to {last_day:%Y-%m-%d}",
        generated_at=datetime.now(),
        sections=[totals_section, breakdown_section],
    )
