"""Shared "out / low / ok" stock-status classification for pos_app's
screens (Home's badge counts, My Local Stock's rows/filters) - one place
so both agree on the exact same thresholds as
shared.models.Product.is_below_critical_stock.
"""

from __future__ import annotations

from shared.models import Product


def stock_status(product: Product) -> str:
    if product.stock_quantity <= 0:
        return "out"
    if product.is_below_critical_stock:
        return "low"
    return "ok"
