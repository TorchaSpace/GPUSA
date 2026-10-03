"""Orchestrates a checkout: cart -> database write -> receipt build -> print.

This is the one place that calls across layers (database/, shared/builders,
pos_app/export) so the GUI itself only ever calls one function to finalize
a sale, and each layer underneath stays independently testable.
"""

from __future__ import annotations

from database import transaction_repository
from pos_app.export.receipt_printer import print_receipt
from shared.builders.receipt_builder import build_receipt
from shared.auth import Actor
from shared.models import UNASSIGNED, StockLocation, Transaction


def complete_sale(pending_transaction: Transaction, location: StockLocation = UNASSIGNED,
                  cashier: Actor | None = None) -> Transaction:
    """Finalize `pending_transaction`: write it + deduct stock, then print a receipt.

    Stock comes off `location` - this terminal's dealership shelf.
    Returns the finalized Transaction (with id/created_at populated).
    Raises database.exceptions.InsufficientStockError without printing
    anything if stock ran out from under the cart (e.g. a race with
    another concurrent sale).
    """
    finalized = transaction_repository.finalize_transaction(pending_transaction, location, cashier)
    receipt = build_receipt(finalized)
    print_receipt(receipt)
    return finalized
