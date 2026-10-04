"""Product cost maths (no Qt, no database): the weighted-average unit cost
kept as purchase orders are received, cost entry validation, and margin.

Costs are stored as 2-decimal floats (products.cost_price); every
calculation here goes through Decimal and rounds half-up, so 1.005 becomes
1.01 and float noise never tips a cent.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from shared.formatting import MAX_AMOUNT
from shared.i18n import UserError

_CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def to_cents(value) -> int:
    """A money amount -> whole cents, half-up (on the decimal value)."""
    return int((_dec(value).quantize(_CENT, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def weighted_average_cost(on_hand: int, old_cost: float, received_qty: int, unit_price: float) -> float:
    """The new unit cost after `received_qty` units arrive at `unit_price`:

        (on_hand * old_cost + received_qty * unit_price) / (on_hand + received_qty)

    `on_hand` is the network-wide total BEFORE the receipt. With nothing on
    hand - or no cost on record yet (0 = unknown, not "free"; averaging the
    unknown in as 0 would understate the cost) - the new cost is simply the
    price paid. Rounded to cents, half-up."""
    on_hand = max(0, int(on_hand))
    received_qty = int(received_qty)
    if received_qty <= 0:
        raise ValueError("received quantity must be greater than 0")
    price = _dec(unit_price)
    old = _dec(old_cost)
    if on_hand == 0 or old <= 0:
        new = price
    else:
        new = (on_hand * old + received_qty * price) / (on_hand + received_qty)
    return float(new.quantize(_CENT, rounding=ROUND_HALF_UP))


def clean_cost(value) -> float:
    """Validate a typed product cost: a finite number, 0 (= unknown) or more,
    at most MAX_AMOUNT, rounded half-up to cents. Raises UserError."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise UserError("err.cost_number")
    if isinstance(value, float) and not math.isfinite(value):
        raise UserError("err.cost_number")
    number = _dec(value)
    if not number.is_finite():
        raise UserError("err.cost_number")
    if number < 0:
        raise UserError("err.cost_negative")
    if number > _dec(MAX_AMOUNT):
        raise UserError("err.cost_too_big")
    return float(number.quantize(_CENT, rounding=ROUND_HALF_UP))


def cost_exceeds_price(cost: float, price: float) -> bool:
    """True when a known cost is above the selling price (a loss on every
    sale) - the product form warns, it does not refuse."""
    return cost > 0 and to_cents(cost) > to_cents(price)


def margin_percent(profit_cents: int, revenue_cents: int) -> float | None:
    """profit / revenue as a percentage, or None when there is no revenue
    to divide by."""
    if revenue_cents <= 0:
        return None
    return profit_cents * 100 / revenue_cents


def unit_margin_percent(price: float, cost: float) -> float | None:
    """Margin of one unit sold at `price` that cost `cost`; None when the
    cost is unknown (0) or the price is 0."""
    if cost <= 0 or price <= 0:
        return None
    return margin_percent(to_cents(price) - to_cents(cost), to_cents(price))
