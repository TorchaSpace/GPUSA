"""Stock movements that are NOT a sale: receiving and dispatching goods
at one location.

Since per-location stock (see database/stock_repository.py) this is a
thin, stable facade over stock_repository - kept so depot_app's services
and older callers keep their function names. A receipt or dispatch
always happens somewhere: depot_app passes its own warehouse; the
default, UNASSIGNED, is only for callers with no location at all.
`products.stock_quantity` (the company total) moves with every call.
"""

from __future__ import annotations

from database import stock_repository
from shared.auth import Actor
from shared.models import UNASSIGNED, StockLocation


def receive_stock(barcode: str, quantity: int, note: str | None = None,
                  location: StockLocation = UNASSIGNED, actor: Actor | None = None,
                  reference: str | None = None, bin_code: str | None = None) -> None:
    """Record newly received inventory at `location`.

    Raises ProductNotFoundError if `barcode` doesn't exist - depot staff
    receiving an unrecognized barcode should be told to add the product
    first (admin_app), not have stock silently created for it. Raises
    ValueError for a non-positive quantity, UnknownLocationError for a
    location that doesn't exist.
    """
    stock_repository.receive(location, barcode, quantity, note, actor=actor, reference=reference, bin_code=bin_code)


def dispatch_stock(barcode: str, quantity: int, note: str | None = None,
                   location: StockLocation = UNASSIGNED, actor: Actor | None = None,
                   reference: str | None = None, bin_code: str | None = None) -> None:
    """Record goods leaving the company from `location` (damaged/returned -
    not a sale, and not a shipment to a dealership).

    Raises InsufficientStockError (writing nothing) if `quantity` exceeds
    what's on hand AT THAT LOCATION - never let stock go negative,
    regardless of which app is removing it. Raises ProductNotFoundError
    for an unknown barcode, ValueError for a non-positive quantity.
    """
    stock_repository.dispatch(location, barcode, quantity, note, actor=actor, reference=reference, bin_code=bin_code)


def list_recent_movements(limit: int = 50, movement_type: str | None = None,
                          location: StockLocation | None = None) -> list[dict]:
    """The most recent stock movements, newest first (optionally one
    direction, optionally one location) - see
    stock_repository.list_movements() for the dict keys."""
    return stock_repository.list_movements(limit=limit, location=location, movement_type=movement_type)
