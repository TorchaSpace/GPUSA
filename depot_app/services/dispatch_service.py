"""Orchestrates dispatch: GUI input -> database write. Mirrors receiving_service.py."""

from __future__ import annotations

from database import inventory_repository
from shared.auth import Actor
from shared.models import UNASSIGNED, StockLocation


def dispatch(barcode: str, quantity: int, note: str | None = None, location: StockLocation = UNASSIGNED,
             actor: Actor | None = None) -> None:
    """Goods leave the company from `location` (the depot's own warehouse)."""
    inventory_repository.dispatch_stock(barcode, quantity, note, location=location, actor=actor)
