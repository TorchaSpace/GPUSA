"""The depot Console's Reports page: a stock-movement report for one
warehouse over a date range - pure (no Qt, no SQL), so the screen, the
Excel export and the PDF export all show the same numbers.

Input is stock_repository.list_movements() rows for that warehouse and
period. Every movement is either IN (units arrived at the warehouse) or
OUT (units left it); `reason` says why (see schema.sql). Per product the
report totals in / out / net and splits them by kind:
  Receipts          supplier receipts (Floor Inbound)
  Written out       Floor Outbound (damaged, returned - left the company)
  Shipped out       loaded onto shipments to dealerships
  Returned          shipments cancelled after dispatch, goods back
  Transfers in/out  moved on paper between locations (Admin)
  Count +/-         stock counts that corrected the level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# (column key, label, movement_type, reason)
KINDS = (
    ("receipts", "Receipts", "receive", "receive"),
    ("written_out", "Written out", "dispatch", "dispatch"),
    ("shipped", "Shipped out", "dispatch", "shipment"),
    ("returned", "Returned", "receive", "shipment"),
    ("transfer_in", "Transfers in", "receive", "transfer"),
    ("transfer_out", "Transfers out", "dispatch", "transfer"),
    ("count_up", "Count +", "receive", "count"),
    ("count_down", "Count −", "dispatch", "count"),
)
_KIND_OF = {(movement_type, reason): key for key, _label, movement_type, reason in KINDS}


def kind_of(movement: dict) -> str:
    """Which KINDS column a movement belongs to; 'other_in' / 'other_out'
    for anything unexpected (e.g. a legacy row with no reason)."""
    key = _KIND_OF.get((movement["movement_type"], movement.get("reason")))
    if key:
        return key
    return "other_in" if movement["movement_type"] == "receive" else "other_out"


@dataclass
class ProductLine:
    barcode: str
    name: str
    units_in: int = 0
    units_out: int = 0
    movements: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    @property
    def net(self) -> int:
        return self.units_in - self.units_out


@dataclass
class MovementReport:
    site_label: str
    start: date
    end: date  # inclusive
    lines: list[ProductLine]
    movements: list[dict]  # newest first, as given

    @property
    def title(self) -> str:
        return f"Stock movements · {self.site_label}"

    @property
    def period_text(self) -> str:
        if self.start == self.end:
            return self.start.strftime("%d.%m.%Y")
        return f"{self.start.strftime('%d.%m.%Y')} – {self.end.strftime('%d.%m.%Y')}"

    @property
    def units_in(self) -> int:
        return sum(line.units_in for line in self.lines)

    @property
    def units_out(self) -> int:
        return sum(line.units_out for line in self.lines)

    @property
    def net(self) -> int:
        return self.units_in - self.units_out

    def kind_total(self, key: str) -> int:
        return sum(line.by_kind.get(key, 0) for line in self.lines)

    @property
    def kinds_present(self) -> list[tuple[str, str]]:
        """(key, label) of the KINDS columns with any units, in KINDS order,
        plus Other in/out if present - keeps the tables narrow."""
        present = [(key, label) for key, label, _t, _r in KINDS if self.kind_total(key)]
        for key, label in (("other_in", "Other in"), ("other_out", "Other out")):
            if self.kind_total(key):
                present.append((key, label))
        return present


def build_movement_report(movements: list[dict], site_label: str, start: date, end: date) -> MovementReport:
    """Group `movements` per product (busiest first, then by name)."""
    lines: dict[str, ProductLine] = {}
    for m in movements:
        line = lines.setdefault(m["barcode"], ProductLine(m["barcode"], m["product_name"]))
        qty = int(m["quantity"])
        if m["movement_type"] == "receive":
            line.units_in += qty
        else:
            line.units_out += qty
        line.movements += 1
        key = kind_of(m)
        line.by_kind[key] = line.by_kind.get(key, 0) + qty
    ordered = sorted(lines.values(), key=lambda l: (-(l.units_in + l.units_out), l.name.casefold()))
    return MovementReport(site_label, start, end, ordered, list(movements))
