"""Orchestrates receiving: GUI input -> database write -> any confirmation UI needs.

Thin today (inventory_repository.receive_stock() does the real work),
but kept as its own layer - mirroring pos_app/services/checkout_service.py -
so the GUI never calls database/ directly, and this is the one place
that changes if receiving ever needs more than a single repository call
(e.g. printing a goods-received slip).
"""

from __future__ import annotations

from database import inventory_repository
from shared.auth import Actor
from shared.models import UNASSIGNED, StockLocation


def receive(barcode: str, quantity: int, note: str | None = None, location: StockLocation = UNASSIGNED,
            actor: Actor | None = None, reference: str | None = None, bin_code: str | None = None) -> None:
    """New goods arrive at `location` (the depot's own warehouse)."""
    inventory_repository.receive_stock(barcode, quantity, note, location=location, actor=actor,
                                       reference=reference, bin_code=bin_code)
