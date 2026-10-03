"""All Transaction CRUD/queries, including the atomic stock-deduction on checkout.

The only place SQL for `transactions` / `transaction_items` is allowed
to live.
"""

from __future__ import annotations
from datetime import datetime

from database.connection import connection_scope
from database.exceptions import InsufficientStockError, ProductNotFoundError, TransactionNotFoundError
from database.stock_repository import change_level, level_in, require_location
from shared.auth import Actor, actor_label
from shared.models import UNASSIGNED, LineItem, StockLocation, Transaction


def finalize_transaction(transaction: Transaction, location: StockLocation = UNASSIGNED,
                         cashier: Actor | None = None) -> Transaction:
    """Persist a completed sale and deduct stock, atomically.

    Stock comes off `location`'s shelf - the selling terminal's
    dealership (pos_app passes it); UNASSIGNED only for a terminal with
    no dealership identity. The company total drops by the same amount
    (see database/stock_repository.py), and the dealership's code is
    stored on the sale.

    Runs as a single DB transaction: insert the transaction + its line
    items, and decrement each product's stock at `location`, all or
    nothing. Raises InsufficientStockError (without writing anything) if
    any line item needs more than `location` holds, and ProductNotFoundError
    if a line item references a barcode that no longer exists (e.g. an
    admin deleted it mid-sale) - either way nothing is written.

    BEGIN IMMEDIATE takes the write lock up front rather than on first
    write, since WAL mode still serializes writers and we want a clean
    "the other checkout committed first, stock ran out" failure here, not
    a partially-applied write if two checkouts land at once.

    Returns the same Transaction with `id` and `created_at` populated.
    """
    if not transaction.items:
        raise ValueError("Cannot finalize a transaction with no line items")

    wanted: dict[str, int] = {}
    for item in transaction.items:
        wanted[item.product_barcode] = wanted.get(item.product_barcode, 0) + item.quantity

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            require_location(conn, location)
            # Validate every product BEFORE writing anything - a sale is
            # all-or-nothing, never "some items deducted, then it failed".
            # Summed per product, so the same barcode on two lines can't
            # sneak past the check.
            for barcode, quantity in wanted.items():
                if conn.execute("SELECT 1 FROM products WHERE barcode = ?", (barcode,)).fetchone() is None:
                    raise ProductNotFoundError(barcode)
                available = level_in(conn, location, barcode)
                if quantity > available:
                    raise InsufficientStockError(barcode, quantity, available, location.label)

            cursor = conn.execute(
                "INSERT INTO transactions (total, dealership_code, cashier) VALUES (?, ?, ?)",
                (transaction.total, None if location.is_unassigned else location.code, actor_label(cashier)),
            )
            transaction_id = cursor.lastrowid

            for item in transaction.items:
                conn.execute(
                    "INSERT INTO transaction_items "
                    "(transaction_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        transaction_id,
                        item.product_barcode,
                        item.product_name_at_sale,
                        item.unit_price_at_sale,
                        item.quantity,
                    ),
                )
            for barcode, quantity in wanted.items():
                change_level(conn, location, barcode, -quantity, change_total=True)

            created_at_row = conn.execute(
                "SELECT created_at FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    return Transaction(
        id=transaction_id,
        created_at=_parse_timestamp(created_at_row["created_at"]),
        items=list(transaction.items),
        dealership_code=None if location.is_unassigned else location.code,
        cashier=actor_label(cashier),
    )


def get_by_id(transaction_id: int) -> Transaction:
    """Return one Transaction by id, or raise TransactionNotFoundError.

    Same two-query header-then-items shape as list_between() - see that
    function's docstring for why.
    """
    with connection_scope() as conn:
        header = conn.execute(
            "SELECT id, created_at, dealership_code, cashier FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if header is None:
            raise TransactionNotFoundError(transaction_id)

        item_rows = conn.execute(
            "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity "
            "FROM transaction_items WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchall()

    items = [
        LineItem(
            product_barcode=item["product_barcode"],
            product_name_at_sale=item["product_name_at_sale"],
            unit_price_at_sale=item["unit_price_at_sale"],
            quantity=item["quantity"],
        )
        for item in item_rows
    ]
    return Transaction(id=header["id"], created_at=_parse_timestamp(header["created_at"]), items=items,
                       dealership_code=header["dealership_code"], cashier=header["cashier"])


def _parse_timestamp(value: str) -> datetime:
    """Parse a created_at value written by schema.sql's default
    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), e.g. "2026-09-24T12:34:56.789Z"),
    tolerant of Python versions whose datetime.fromisoformat() is strict
    about fractional-second width (SQLite's %f gives 3 digits; some
    Pythons only accept 3 or 6, never fewer/other).
    """
    value = value.rstrip("Z")
    if "." in value:
        date_part, frac = value.split(".", 1)
        value = f"{date_part}.{(frac + '000000')[:6]}"
    return datetime.fromisoformat(value)


def list_between(start: datetime, end: datetime) -> list[Transaction]:
    """Return all transactions in [start, end), for the Admin sales report tab.

    Two queries per call (transaction headers, then each one's line
    items) rather than a single JOIN - simpler to map back into
    Transaction/LineItem instances, and sales report ranges are a
    manager-triggered, infrequent read, not a hot path worth
    micro-optimizing.
    """
    with connection_scope() as conn:
        header_rows = conn.execute(
            "SELECT id, created_at, dealership_code, cashier FROM transactions "
            "WHERE created_at >= ? AND created_at < ? "
            "ORDER BY created_at",
            (start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S")),
        ).fetchall()

        transactions: list[Transaction] = []
        for header in header_rows:
            item_rows = conn.execute(
                "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity "
                "FROM transaction_items WHERE transaction_id = ?",
                (header["id"],),
            ).fetchall()
            items = [
                LineItem(
                    product_barcode=item["product_barcode"],
                    product_name_at_sale=item["product_name_at_sale"],
                    unit_price_at_sale=item["unit_price_at_sale"],
                    quantity=item["quantity"],
                )
                for item in item_rows
            ]
            transactions.append(
                Transaction(
                    id=header["id"],
                    created_at=_parse_timestamp(header["created_at"]),
                    items=items,
                    dealership_code=header["dealership_code"],
                    cashier=header["cashier"],
                )
            )
    return transactions
