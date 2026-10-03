"""Test helper: the per-location stock invariant (database/stock_repository.py)."""

from __future__ import annotations

from database.connection import connection_scope


def assert_totals_consistent() -> None:
    """products.stock_quantity == SUM(stock_levels) + units on the road, for every product."""
    with connection_scope() as conn:
        rows = conn.execute(
            "SELECT p.barcode, p.stock_quantity, "
            "(SELECT COALESCE(SUM(quantity), 0) FROM stock_levels l WHERE l.product_barcode = p.barcode), "
            "(SELECT COALESCE(SUM(sl.expected_qty), 0) FROM shipment_lines sl JOIN shipments s ON s.id = sl.shipment_id "
            " WHERE sl.product_barcode = p.barcode AND s.status = 'in_transit' AND s.stock_moved = 1) "
            "FROM products p"
        ).fetchall()
    bad = [(r[0], r[1], r[2], r[3]) for r in rows if r[1] != r[2] + r[3]]
    assert not bad, f"total != levels + in transit for (barcode, total, levels, transit): {bad}"
