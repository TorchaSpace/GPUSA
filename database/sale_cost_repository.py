"""Reading back the cost snapshot of past sales (transaction_items.
unit_cost_at_sale / cost_known, migration v4).

The snapshot is WRITTEN by transaction_repository.finalize_transaction
(it asks product_repository.costs_for() for each barcode and stores the
result with the line). Reading it belongs in transaction_repository's own
readers, but until those select the two columns this module fills them in:
attach_costs() takes the Transactions list_between()/get_by_id() returned
and sets `unit_cost_at_sale` / `cost_known` on their LineItems, so
profit reports work today. It is harmless once the readers do it
themselves (it just writes the same values again).
"""

from __future__ import annotations

from database.connection import connection_scope
from shared.models import Transaction

_CHUNK = 500  # stays well under SQLite's bound-variable limit


def attach_costs(transactions: list[Transaction]) -> list[Transaction]:
    """Set unit_cost_at_sale / cost_known on every LineItem of `transactions`
    from the database and return the same list. Lines are matched to their
    stored rows by order within the sale, falling back to the first unused
    row for the same product; a line with no stored row keeps "cost
    unknown". Transactions without an id (never saved) are left alone."""
    by_id = {t.id: t for t in transactions if t.id is not None}
    if not by_id:
        return transactions
    ids = list(by_id)
    rows: dict[int, list] = {}
    with connection_scope() as conn:
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start:start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for row in conn.execute(
                "SELECT transaction_id, product_barcode, unit_cost_at_sale, cost_known FROM transaction_items "
                f"WHERE transaction_id IN ({marks}) ORDER BY id",
                chunk,
            ):
                rows.setdefault(row["transaction_id"], []).append(row)
    for transaction_id, transaction in by_id.items():
        stored = list(rows.get(transaction_id, []))
        for index, item in enumerate(transaction.items):
            match = None
            if index < len(stored) and stored[index] is not None and stored[index]["product_barcode"] == item.product_barcode:
                match = index
            else:
                match = next((i for i, r in enumerate(stored) if r is not None and r["product_barcode"] == item.product_barcode), None)
            if match is None:
                continue
            row = stored[match]
            stored[match] = None
            item.unit_cost_at_sale = float(row["unit_cost_at_sale"])
            item.cost_known = bool(row["cost_known"])
    return transactions
