"""Regression tests for shared.builders.receipt_builder."""

from __future__ import annotations

from shared.builders.receipt_builder import build_receipt
from shared.constants import RECEIPT_WIDTH_CHARS
from shared.models import LineItem, Transaction


def _sale(**fields) -> Transaction:
    return Transaction(
        items=[
            LineItem(product_barcode="B1", product_name_at_sale="A very long product name " * 3,
                     unit_price_at_sale=2.5, quantity=2),
            LineItem(product_barcode="B2", product_name_at_sale="Rice", unit_price_at_sale=1.0, quantity=1),
        ],
        **fields,
    )


def test_receipt_lines_do_not_exceed_receipt_width():
    receipt = build_receipt(_sale(id=7, payment_method="card"), "Store", ["Line 1"])
    assert all(len(line) <= RECEIPT_WIDTH_CHARS for line in receipt.lines)


def test_receipt_total_matches_transaction_total():
    receipt = build_receipt(_sale(), "Store", [])
    assert receipt.lines[-1].startswith("TOTAL") and receipt.lines[-1].endswith("6.00")


def test_receipt_says_how_the_sale_was_paid():
    assert build_receipt(_sale(payment_method="cash"), "Store", []).lines[-1].split() == ["PAID", "Cash"]
