"""Refunds against earlier sales (`sale_returns` / `sale_return_items`, migration v9).

The sale is never edited. A refund names the sale, the products and
quantities coming back, why, and who approved it; it is written in ONE
BEGIN IMMEDIATE transaction together with its effect:
- units marked `restock` go back on the sale's dealership shelf (the
  company total rises by the same amount, as a sale lowered it); damaged
  ones (restock False) only move money;
- an `admin_audit` row ("refunded") records requester and approver.
The money goes back the way it came (the sale's payment_method).

A manager (depot manager or administrator) must approve: `approved_by` is
re-checked against the accounts table inside the transaction, like a
cashier is on a sale. `client_uuid` makes a re-sent refund store once.
Quantities are checked against sold minus already returned, so the same
units can never be refunded twice, even from two tills at once.

transaction_repository.list_between() reads sales net of these refunds;
period_totals() here gives the day-close report the refunds paid out in a
window.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from database import account_repository, audit_repository
from database.connection import connection_scope
from database.exceptions import ReturnApprovalError, ReturnQuantityError, SessionInvalidError, TransactionNotFoundError
from database.stock_repository import change_level
from shared.auth import AREA_DEPOT_CONSOLE, Actor, actor_label
from shared.formatting import to_db_timestamp
from shared.i18n import UserError
from shared.models import ReturnableLine, ReturnLine, SaleReturn, StockLocation, UNASSIGNED

MAX_REASON_LENGTH = 200


def _returned(conn: sqlite3.Connection, transaction_id: int) -> dict[str, int]:
    return {
        row[0]: int(row[1])
        for row in conn.execute(
            "SELECT i.product_barcode, SUM(i.quantity) FROM sale_return_items i "
            "JOIN sale_returns r ON r.id = i.return_id WHERE r.transaction_id = ? GROUP BY i.product_barcode",
            (transaction_id,),
        )
    }


def _sold_lines(conn: sqlite3.Connection, transaction_id: int) -> dict[str, tuple[str, float, int]]:
    """barcode -> (name, unit price (first line's), total quantity)."""
    lines: dict[str, tuple[str, float, int]] = {}
    for row in conn.execute(
        "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity FROM transaction_items "
        "WHERE transaction_id = ? ORDER BY id", (transaction_id,)
    ):
        name, price, qty = lines.get(row["product_barcode"], (row["product_name_at_sale"], row["unit_price_at_sale"], 0))
        lines[row["product_barcode"]] = (name, price, qty + row["quantity"])
    return lines


def returnable_lines(transaction_id: int) -> list[ReturnableLine]:
    """What of this sale can still come back, per product (TransactionNotFoundError if no such sale)."""
    with connection_scope() as conn:
        if conn.execute("SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)).fetchone() is None:
            raise TransactionNotFoundError(transaction_id)
        returned = _returned(conn, transaction_id)
        return [
            ReturnableLine(barcode, name, price, sold, returned.get(barcode, 0))
            for barcode, (name, price, sold) in _sold_lines(conn, transaction_id).items()
        ]


def create(transaction_id: int, lines: list[tuple[str, int, bool]], reason: str, approved_by: Actor,
           requested_by: Actor | None = None, client_uuid: str | None = None) -> SaleReturn:
    """Refund `lines` - (barcode, quantity, restock) - of sale `transaction_id`.
    Errors (nothing written): ValueError/UserError for an empty list, a quantity
    that is not a positive whole number or a missing/overlong reason;
    TransactionNotFoundError; ReturnQuantityError; ReturnApprovalError."""
    reason = (reason or "").strip()
    if not reason:
        raise UserError("err.return_reason")
    if len(reason) > MAX_REASON_LENGTH:
        raise UserError("err.return_reason_long", n=MAX_REASON_LENGTH)
    wanted: dict[str, list[int]] = {}  # barcode -> [restocked, written off]
    for barcode, quantity, restock in lines:
        if isinstance(quantity, bool) or int(quantity) != quantity or quantity <= 0:
            raise UserError("err.return_lines")
        wanted.setdefault(barcode, [0, 0])[0 if restock else 1] += int(quantity)
    if not wanted:
        raise UserError("err.return_lines")
    client_uuid = (client_uuid or "").strip() or uuid.uuid4().hex

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute("SELECT id FROM sale_returns WHERE client_uuid = ?", (client_uuid,)).fetchone()
            if existing is not None:
                conn.execute("ROLLBACK")
                return get(existing["id"])
            sale = conn.execute(
                "SELECT dealership_code, payment_method FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
            if sale is None:
                raise TransactionNotFoundError(transaction_id)
            try:
                account_repository.require_actor_allowed(conn, approved_by, AREA_DEPOT_CONSOLE)
            except SessionInvalidError:
                raise ReturnApprovalError() from None
            if approved_by is None:
                raise ReturnApprovalError()
            if requested_by is not None:
                account_repository.require_actor_allowed(conn, requested_by, "pos")

            sold = _sold_lines(conn, transaction_id)
            returned = _returned(conn, transaction_id)
            for barcode, (restocked, written_off) in wanted.items():
                if barcode not in sold:
                    raise ReturnQuantityError(barcode, restocked + written_off, 0)
                available = sold[barcode][2] - returned.get(barcode, 0)
                if restocked + written_off > available:
                    raise ReturnQuantityError(sold[barcode][0], restocked + written_off, available)

            total = round(sum(sold[b][1] * (r + w) for b, (r, w) in wanted.items()), 2)
            location = UNASSIGNED if sale["dealership_code"] is None else StockLocation.dealership(sale["dealership_code"])
            cursor = conn.execute(
                "INSERT INTO sale_returns (transaction_id, dealership_code, total, payment_method, reason, "
                "requested_by, approved_by, client_uuid) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (transaction_id, sale["dealership_code"], total, sale["payment_method"], reason,
                 actor_label(requested_by), actor_label(approved_by), client_uuid),
            )
            return_id = cursor.lastrowid
            for barcode, (restocked, written_off) in wanted.items():
                name, price, _ = sold[barcode]
                for quantity, restock in ((restocked, True), (written_off, False)):
                    if quantity:
                        conn.execute(
                            "INSERT INTO sale_return_items (return_id, product_barcode, product_name_at_sale, "
                            "unit_price_at_sale, quantity, restock) VALUES (?, ?, ?, ?, ?, ?)",
                            (return_id, barcode, name, price, quantity, int(restock)),
                        )
                if restocked:
                    change_level(conn, location, barcode, restocked, change_total=True)
            audit_repository.record(conn, approved_by, "refunded", "sale", f"#{transaction_id}",
                                    f"RF-{return_id:05d} · {total:.2f} · {reason}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return get(return_id)


def get(return_id: int) -> SaleReturn:
    with connection_scope() as conn:
        header = conn.execute("SELECT * FROM sale_returns WHERE id = ?", (return_id,)).fetchone()
        if header is None:
            raise TransactionNotFoundError(return_id)
        return _load(conn, [header])[0]


def list_for_transaction(transaction_id: int) -> list[SaleReturn]:
    with connection_scope() as conn:
        headers = conn.execute(
            "SELECT * FROM sale_returns WHERE transaction_id = ? ORDER BY id", (transaction_id,)
        ).fetchall()
        return _load(conn, headers)


def list_between(start: datetime, end: datetime, dealership_code: str | None = None) -> list[SaleReturn]:
    """Refunds paid out in [start, end) (LOCAL times, as transaction_repository.list_between)."""
    sql = "SELECT * FROM sale_returns WHERE created_at >= ? AND created_at < ?"
    params: list = [to_db_timestamp(start), to_db_timestamp(end)]
    if dealership_code is not None:
        sql += " AND dealership_code = ?"
        params.append(dealership_code)
    with connection_scope() as conn:
        return _load(conn, conn.execute(sql + " ORDER BY id", params).fetchall())


def _load(conn: sqlite3.Connection, headers: list[sqlite3.Row]) -> list[SaleReturn]:
    from database.transaction_repository import _as_local, _parse_timestamp  # same timestamp rules as sales

    result = []
    for header in headers:
        items = conn.execute(
            "SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity, restock "
            "FROM sale_return_items WHERE return_id = ? ORDER BY id", (header["id"],)
        ).fetchall()
        result.append(SaleReturn(
            id=header["id"], transaction_id=header["transaction_id"], reason=header["reason"],
            created_at=_as_local(_parse_timestamp(header["created_at"])),
            dealership_code=header["dealership_code"], payment_method=header["payment_method"],
            requested_by=header["requested_by"], approved_by=header["approved_by"], client_uuid=header["client_uuid"],
            lines=[ReturnLine(i["product_barcode"], i["product_name_at_sale"], i["unit_price_at_sale"],
                              i["quantity"], bool(i["restock"])) for i in items],
        ))
    return result
