"""Orchestrates a checkout: cart -> database write -> receipt build -> print.

This is the one place that calls across layers (database/, shared/builders,
pos_app/export) so the GUI itself only ever calls one function to finalize
a sale, and each layer underneath stays independently testable.
"""

from __future__ import annotations

from database import settings_repository, transaction_repository
from pos_app.export.receipt_printer import print_receipt
from shared.builders.receipt_builder import build_receipt
from shared.auth import Actor
from shared.models import UNASSIGNED, StockLocation, Transaction


def complete_sale(pending_transaction: Transaction, location: StockLocation = UNASSIGNED,
                  cashier: Actor | None = None) -> Transaction:
    """Finalize `pending_transaction`: write it + deduct stock, then print a receipt.

    Stock comes off `location` - this terminal's dealership shelf.
    Returns the finalized Transaction (with id/created_at populated).
    Nothing is printed, and nothing was written, when the database refuses
    the sale - finalize_transaction() re-checks everything authoritatively
    and its errors propagate unchanged: PriceChangedError (a price moved
    since the line was added), ProductInactiveError / ProductNotFoundError,
    InsufficientStockError (e.g. a race with another sale),
    SessionInvalidError (the cashier's account was switched off) and
    DealershipInactiveError (the till's dealership was switched off).
    """
    finalized = transaction_repository.finalize_transaction(pending_transaction, location, cashier)
    profile = settings_repository.safe_store_profile()
    receipt = build_receipt(finalized, profile.name, profile.address_lines)
    print_receipt(receipt)
    return finalized
