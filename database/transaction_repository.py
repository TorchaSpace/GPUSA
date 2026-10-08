"""All Transaction CRUD/queries, including the atomic stock-deduction on checkout.

The only place SQL for `transactions` / `transaction_items` is allowed
to live.
"""

from __future__ import annotations
import dataclasses
import sqlite3
import uuid
from datetime import datetime

from database import account_repository, activity_repository, product_repository
from database.connection import connection_scope
from database.exceptions import (
    DealershipInactiveError,
    InsufficientStockError,
    PriceChangedError,
    ProductInactiveError,
    ProductNotFoundError,
    TransactionNotFoundError,
)
from database.stock_repository import change_level, level_in, require_location
from shared.auth import AREA_POS, Actor, actor_label
from shared.formatting import to_db_timestamp
from shared.models import PAYMENT_METHODS, UNASSIGNED, LineItem, StockLocation, Transaction


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
    nothing. THIS is where a sale is authoritative - the POS page's own
    pre-check is only a courtesy, because a price, a product, an account or
    a dealership can change between that check and the charge. Inside the
    one BEGIN IMMEDIATE transaction it re-reads everything and refuses,
    writing nothing, with:
    - DealershipInactiveError  if `location` is a dealership switched off in Admin;
    - SessionInvalidError      if `cashier` no longer has an active account
                               that may use POS (switched off, employee
                               deactivated, role changed);
    - ProductNotFoundError / ProductInactiveError  for a barcode that is
                               gone / deactivated;
    - PriceChangedError        if any line's unit_price_at_sale differs (in
                               cents) from the product's price right now -
                               it lists every such line (barcode, old, new);
    - InsufficientStockError   if any line needs more than `location` holds.
    The cost snapshot (transaction_items.unit_cost_at_sale / cost_known) is
    taken from the same read: cost_known only when cost_price > 0.

    BEGIN IMMEDIATE takes the write lock up front rather than on first
    write, since WAL mode still serializes writers and we want a clean
    "the other checkout committed first, stock ran out" failure here, not
    a partially-applied write if two checkouts land at once.

    `transaction.client_uuid` (made here when the till did not send one) is
    the sale's key: a sale whose key is already stored is NOT sold again -
    the stored sale is returned, nothing is written (a retry after a lost
    answer, or an offline till uploading twice, can never double a sale).

    `transaction.payment_method` ("card" / "cash", see
    shared.models.PAYMENT_METHODS) is stored as given; anything else is a
    ValueError before the database is touched.

    Returns the same Transaction with `id` and `created_at` populated.
    """
    if not transaction.items:
        raise ValueError("Cannot finalize a transaction with no line items")
    if transaction.payment_method is not None and transaction.payment_method not in PAYMENT_METHODS:
        raise ValueError(f"Unknown payment method: {transaction.payment_method!r}")

    client_uuid = (transaction.client_uuid or "").strip() or uuid.uuid4().hex
    wanted: dict[str, int] = {}
    for item in transaction.items:
        wanted[item.product_barcode] = wanted.get(item.product_barcode, 0) + item.quantity

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            already = conn.execute("SELECT id FROM transactions WHERE client_uuid = ?", (client_uuid,)).fetchone()
            if already is not None:
                conn.execute("ROLLBACK")
                return get_by_id(already["id"])
            require_location(conn, location)
            if location.kind == "dealership":
                shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = ?", (location.code,)).fetchone()
                if shop is not None and not shop["is_active"]:
                    raise DealershipInactiveError(shop["name"])
            account_repository.require_actor_allowed(conn, cashier, AREA_POS)

            # Validate every product BEFORE writing anything - a sale is
            # all-or-nothing, never "some items deducted, then it failed".
            # Stock is summed per product, so the same barcode on two lines
            # can't sneak past the check.
            products: dict[str, sqlite3.Row] = {}
            for barcode in wanted:
                row = conn.execute("SELECT * FROM products WHERE barcode = ?", (barcode,)).fetchone()
                if row is None:
                    raise ProductNotFoundError(barcode)
                if not row["is_active"]:
                    raise ProductInactiveError(barcode)
                products[barcode] = row
            moved = [
                (item.product_barcode, item.unit_price_at_sale, products[item.product_barcode]["price"])
                for item in transaction.items
                if _cents(item.unit_price_at_sale) != _cents(products[item.product_barcode]["price"])
            ]
            if moved:
                raise PriceChangedError(moved)
            levels: dict[str, int] = {}
            for barcode, quantity in wanted.items():
                available = level_in(conn, location, barcode)
                if quantity > available:
                    raise InsufficientStockError(barcode, quantity, available, location.label)
                levels[barcode] = available

            cursor = conn.execute(
                "INSERT INTO transactions (total, dealership_code, cashier, payment_method, client_uuid) "
                "VALUES (?, ?, ?, ?, ?)",
                (transaction.total, None if location.is_unassigned else location.code, actor_label(cashier),
                 transaction.payment_method, client_uuid),
            )
            transaction_id = cursor.lastrowid

            costs = product_repository.costs_for(list(wanted), conn)
            sold: list[LineItem] = []
            for item in transaction.items:
                # The cost this unit carried at this moment (read inside this
                # transaction); cost_known only when a cost is on record (> 0).
                cost, known = costs[item.product_barcode]
                item = dataclasses.replace(item, unit_cost_at_sale=cost, cost_known=known)
                sold.append(item)
                conn.execute(
                    "INSERT INTO transaction_items "
                    "(transaction_id, product_barcode, product_name_at_sale, unit_price_at_sale, quantity, "
                    "unit_cost_at_sale, cost_known) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        transaction_id,
                        item.product_barcode,
                        item.product_name_at_sale,
                        item.unit_price_at_sale,
                        item.quantity,
                        item.unit_cost_at_sale,
                        int(item.cost_known),
                    ),
                )
            for barcode, quantity in wanted.items():
                left = change_level(conn, location, barcode, -quantity, change_total=True)
                activity_repository.stock_crossing(conn, location, barcode, levels[barcode], left,
                                                   source="pos", actor=cashier)
            activity_repository.record(conn, "sale", source="pos", location=location, actor=cashier,
                                       id=transaction_id, total=transaction.total, units=sum(wanted.values()),
                                       method=transaction.payment_method)

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
        items=sold,
        dealership_code=None if location.is_unassigned else location.code,
        cashier=actor_label(cashier),
        payment_method=transaction.payment_method,
        client_uuid=client_uuid,
    )


def _cents(amount: float) -> int:
    """Prices are compared in whole cents, so 5.0 and 5.004 floats of the
    same price never read as a change."""
    return int(round(float(amount) * 100))


def get_by_id(transaction_id: int) -> Transaction:
    """Return one Transaction by id, or raise TransactionNotFoundError.

    Same two-query header-then-items shape as list_between() - see that
    function's docstring for why.
    """
    with connection_scope() as conn:
        header = conn.execute(
            "SELECT id, created_at, dealership_code, cashier, payment_method, client_uuid FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if header is None:
            raise TransactionNotFoundError(transaction_id)

        item_rows = conn.execute(
            "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity, "
            "unit_cost_at_sale, cost_known FROM transaction_items WHERE transaction_id = ? ORDER BY id",
            (transaction_id,),
        ).fetchall()

    items = [
        LineItem(
            product_barcode=item["product_barcode"],
            product_name_at_sale=item["product_name_at_sale"],
            unit_price_at_sale=item["unit_price_at_sale"],
            quantity=item["quantity"],
            unit_cost_at_sale=item["unit_cost_at_sale"],
            cost_known=bool(item["cost_known"]),
        )
        for item in item_rows
    ]
    return Transaction(id=header["id"], created_at=_parse_timestamp(header["created_at"]), items=items,
                       dealership_code=header["dealership_code"], cashier=header["cashier"],
                       payment_method=header["payment_method"], client_uuid=header["client_uuid"])


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


def _as_local(naive_utc: datetime) -> datetime:
    """A naive UTC datetime (what the database holds) -> naive local time."""
    from datetime import timezone

    return naive_utc.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def list_between(start: datetime, end: datetime, net_of_returns: bool = True) -> list[Transaction]:
    """Return all transactions in [start, end), for the Admin sales report tab.

    By default each sale is read NET of its refunds (database/sale_return_repository.py):
    the returned units come off its lines (taken off the lines in order) and a
    sale with nothing left is left out, so revenue, profit and unit counts in every
    report already exclude what was given back. Refunds count on the SALE's date
    here; the day-close report reads them on the day they were paid out instead
    (net_of_returns=False plus sale_return_repository.list_between).

    `start`/`end` are LOCAL times (a date picker on this machine) and each
    returned transaction's created_at is local too, so a sale at 01:30 in
    Istanbul counts on that calendar day, not the previous UTC one.

    Two queries per call (transaction headers, then each one's line
    items) rather than a single JOIN - simpler to map back into
    Transaction/LineItem instances, and sales report ranges are a
    manager-triggered, infrequent read, not a hot path worth
    micro-optimizing.
    """
    with connection_scope() as conn:
        header_rows = conn.execute(
            "SELECT id, created_at, dealership_code, cashier, payment_method FROM transactions "
            "WHERE created_at >= ? AND created_at < ? "
            "ORDER BY created_at",
            (to_db_timestamp(start), to_db_timestamp(end)),
        ).fetchall()

        transactions: list[Transaction] = []
        for header in header_rows:
            item_rows = conn.execute(
                "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity, "
                "unit_cost_at_sale, cost_known FROM transaction_items WHERE transaction_id = ? ORDER BY id",
                (header["id"],),
            ).fetchall()
            items = [
                LineItem(
                    product_barcode=item["product_barcode"],
                    product_name_at_sale=item["product_name_at_sale"],
                    unit_price_at_sale=item["unit_price_at_sale"],
                    quantity=item["quantity"],
                    unit_cost_at_sale=item["unit_cost_at_sale"],
                    cost_known=bool(item["cost_known"]),
                )
                for item in item_rows
            ]
            if net_of_returns:
                net = _net_items(conn, header["id"], items)
                if items and not net:  # everything on it was refunded
                    continue
                items = net
            transactions.append(
                Transaction(
                    id=header["id"],
                    created_at=_as_local(_parse_timestamp(header["created_at"])),
                    items=items,
                    dealership_code=header["dealership_code"],
                    cashier=header["cashier"],
                    payment_method=header["payment_method"],
                )
            )
    return transactions


def _net_items(conn: sqlite3.Connection, transaction_id: int, items: list[LineItem]) -> list[LineItem]:
    """`items` less the units refunded per product, taken off the lines in order."""
    returned = {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT i.product_barcode, SUM(i.quantity) FROM sale_return_items i "
            "JOIN sale_returns r ON r.id = i.return_id WHERE r.transaction_id = ? GROUP BY i.product_barcode",
            (transaction_id,),
        )
    }
    if not returned:
        return items
    net: list[LineItem] = []
    for item in items:
        take = min(item.quantity, returned.get(item.product_barcode, 0))
        if take:
            returned[item.product_barcode] -= take
        if item.quantity - take > 0:
            net.append(dataclasses.replace(item, quantity=item.quantity - take))
    return net
