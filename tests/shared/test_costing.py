"""shared.costing - weighted-average cost, cost entry validation and margins."""

import pytest

from shared import costing


@pytest.mark.parametrize(
    "on_hand, old, qty, price, expected",
    [
        (100, 10.0, 50, 16.0, 12.0),  # (1000 + 800) / 150
        (150, 12.0, 30, 20.0, 13.33),  # 2400 / 180 = 13.333...
        (0, 7.0, 10, 9.5, 9.5),  # nothing on hand: the price paid, whatever the old cost
        (50, 0.0, 10, 20.0, 20.0),  # cost unknown (0) is not averaged in as free
        (1, 0.01, 1, 0.02, 0.02),  # 0.015 rounds half-up
        (1, 0.01, 1, 0.01, 0.01),
        (3, 1.0, 1, 1.01, 1.0),  # 4.01 / 4 = 1.0025
        (3, 1.0, 1, 1.02, 1.01),  # 4.02 / 4 = 1.005 -> 1.01, not binary-float 1.00
        (10, 100.0, 1, 100.0, 100.0),
        (999_999, 5.0, 1, 5.0, 5.0),
    ],
)
def test_weighted_average_cost(on_hand, old, qty, price, expected):
    assert costing.weighted_average_cost(on_hand, old, qty, price) == expected


def test_weighted_average_rejects_a_non_positive_receipt():
    for qty in (0, -3):
        with pytest.raises(ValueError):
            costing.weighted_average_cost(10, 5.0, qty, 5.0)


def test_a_negative_on_hand_is_treated_as_nothing():
    assert costing.weighted_average_cost(-4, 5.0, 2, 8.0) == 8.0


@pytest.mark.parametrize("value, expected", [(0, 0.0), (3, 3.0), (1.005, 1.01), (2.994, 2.99), (1.5, 1.5)])
def test_clean_cost_rounds_half_up(value, expected):
    assert costing.clean_cost(value) == expected


@pytest.mark.parametrize("value", [-0.011, float("nan"), float("-inf"), "5", None, True, 1e12])
def test_clean_cost_refuses_nonsense(value):
    with pytest.raises(ValueError):
        costing.clean_cost(value)


def test_cost_above_price_is_flagged_but_unknown_cost_is_not():
    assert costing.cost_exceeds_price(10.01, 10.0)
    assert not costing.cost_exceeds_price(10.0, 10.0)
    assert not costing.cost_exceeds_price(0, 0)  # unknown cost never warns
    assert not costing.cost_exceeds_price(0, 5)


def test_margins_never_divide_by_zero():
    assert costing.margin_percent(1950, 5000) == 39.0
    assert costing.margin_percent(-300, 500) == -60.0
    assert costing.margin_percent(0, 0) is None and costing.margin_percent(5, -1) is None
    assert costing.unit_margin_percent(10.0, 6.0) == 40.0
    assert costing.unit_margin_percent(10.0, 0) is None  # cost unknown
    assert costing.unit_margin_percent(0, 4.0) is None
    assert costing.unit_margin_percent(5.0, 8.0) == -60.0
