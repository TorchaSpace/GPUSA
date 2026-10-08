"""All SQL for dealership stock requests - `stock_requests` - and the
"low at dealerships" view built next to them.

The workflow:
1. A dealership's POS (My Local Stock) asks its depot for a product:
   `create()` -> "open". One open request per dealership and product; a
   second one is DuplicateStockRequestError (change the first instead).
2. The depot's Console > Shipments sees every open request. It either
   plans a shipment from some of them - shipment_repository.create(...,
   request_ids=[...]) marks them "planned" in the same transaction - or
   declines one with a reason (`decline()`).
3. The POS can withdraw a request while it is still open (`cancel()`).
4. A planned request follows its shipment: "delivered" is the shipment's
   own status (joined in, never copied), and cancelling the shipment puts
   its requests back to "open" (`reopen_for_shipment()`, called inside
   shipment_repository.cancel's transaction).

Status changes are `UPDATE ... WHERE status = 'open'`, so two terminals
acting on the same request can't both win: the second gets
StockRequestStateError.

`dealership_shortages()` is the other half of the same question - which
dealership shelves are at/below the reorder level - with what is already
coming and asked for, so the depot and Admin see a shop running dry even
when the company-wide total looks fine.
"""

from __future__ import annotations

import sqlite3

from database import activity_repository
from database.connection import connection_scope
from database.exceptions import (
    DealershipInactiveError,
    DealershipNotFoundError,
    DuplicateStockRequestError,
    StockRequestNotFoundError,
    StockRequestStateError,
)
from database.stock_repository import critical_at, resolve_product
from shared.auth import Actor, actor_label
from shared.i18n import UserError
from shared.models import DealershipShortage, StockLocation, StockRequest
from shared.warehousing import whole_number

MAX_NOTE_LENGTH = 200
MAX_REQUEST_QUANTITY = 100_000

_SELECT = (
    "SELECT r.*, s.status AS shipment_status FROM stock_requests r "
    "LEFT JOIN shipments s ON s.id = r.shipment_id "
)


def _row_to_request(row: sqlite3.Row) -> StockRequest:
    return StockRequest(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        dealership_code=row["dealership_code"],
        dealership_name=row["dealership_name"],
        product_barcode=row["product_barcode"],
        product_name=row["product_name"],
        quantity=row["quantity"],
        note=row["note"],
        status=row["status"],
        requested_by=row["requested_by"],
        decided_by=row["decided_by"],
        decision_note=row["decision_note"],
        shipment_id=row["shipment_id"],
        shipment_status=row["shipment_status"],
    )


def _fetch(conn: sqlite3.Connection, request_id: int) -> StockRequest:
    row = conn.execute(_SELECT + "WHERE r.id = ?", (request_id,)).fetchone()
    if row is None:
        raise StockRequestNotFoundError(request_id)
    return _row_to_request(row)


def _clean_note(note: str | None) -> str | None:
    note = " ".join((note or "").split()) or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise UserError("err.stock_request_note_long", n=MAX_NOTE_LENGTH)
    return note


def _write(fn):
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(conn)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return result


def create(dealership_code: str, barcode: str, quantity, note: str | None = None,
           actor: Actor | None = None) -> StockRequest:
    """A dealership asks for `quantity` of a product. Raises ValueError /
    UserError for a quantity that isn't a whole number above 0 or a note
    that is too long, DealershipNotFoundError / DealershipInactiveError,
    ProductNotFoundError / ProductInactiveError, and
    DuplicateStockRequestError when this dealership already has an open
    request for the product."""
    quantity = whole_number(quantity, "The quantity")
    if quantity <= 0 or quantity > MAX_REQUEST_QUANTITY:
        raise UserError("err.stock_request_qty", n=MAX_REQUEST_QUANTITY)
    note = _clean_note(note)

    def run(conn):
        shop = conn.execute("SELECT name, is_active FROM dealerships WHERE code = ?", (dealership_code,)).fetchone()
        if shop is None:
            raise DealershipNotFoundError(dealership_code)
        if not shop["is_active"]:
            raise DealershipInactiveError(shop["name"])
        stored, name = resolve_product(conn, barcode, active_only=True)
        existing = conn.execute(
            "SELECT id FROM stock_requests WHERE dealership_code = ? AND product_barcode = ? COLLATE NOCASE "
            "AND status = 'open'",
            (dealership_code, stored),
        ).fetchone()
        if existing is not None:
            raise DuplicateStockRequestError(f"RQ-{existing['id']:05d}", name)
        cursor = conn.execute(
            "INSERT INTO stock_requests (dealership_code, dealership_name, product_barcode, product_name, "
            "quantity, note, requested_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dealership_code, shop["name"], stored, name, quantity, note, actor_label(actor)),
        )
        made = _fetch(conn, cursor.lastrowid)
        activity_repository.record(conn, "request_new", severity="notice", source="pos",
                                   location=StockLocation.dealership(dealership_code), actor=actor,
                                   number=made.number, product=name, quantity=quantity)
        return made

    return _write(run)


def get(request_id: int) -> StockRequest:
    with connection_scope() as conn:
        return _fetch(conn, request_id)


def list_requests(statuses: tuple[str, ...] | None = None, dealership_code: str | None = None,
                  limit: int | None = None) -> list[StockRequest]:
    """Requests, newest first. Open ones from a dealership that has since
    been switched off or deleted are still listed (the depot can decline them)."""
    clauses, params = [], []
    if statuses:
        clauses.append(f"r.status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if dealership_code is not None:
        clauses.append("r.dealership_code = ?")
        params.append(dealership_code)
    sql = _SELECT
    if clauses:
        sql += "WHERE " + " AND ".join(clauses) + " "
    sql += "ORDER BY r.created_at DESC, r.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    with connection_scope() as conn:
        return [_row_to_request(row) for row in conn.execute(sql, params).fetchall()]


def list_open() -> list[StockRequest]:
    """Every open request, oldest first - the depot's queue."""
    return list(reversed(list_requests(("open",))))


def count_open(dealership_code: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM stock_requests WHERE status = 'open'"
    params: tuple = ()
    if dealership_code is not None:
        sql += " AND dealership_code = ?"
        params = (dealership_code,)
    with connection_scope() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _close(request_id: int, new_status: str, action: str, actor: Actor | None, note: str | None) -> StockRequest:
    note = _clean_note(note)

    def run(conn):
        current = _fetch(conn, request_id)
        cursor = conn.execute(
            "UPDATE stock_requests SET status = ?, decided_by = ?, decision_note = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND status = 'open'",
            (new_status, actor_label(actor), note, request_id),
        )
        if cursor.rowcount == 0:
            raise StockRequestStateError(current.number, current.status, action)
        if new_status == "declined":
            activity_repository.record(conn, "request_declined", source="depot", actor=actor,
                                       location=StockLocation.dealership(current.dealership_code),
                                       number=current.number, product=current.product_name, reason=note or "")
        return _fetch(conn, request_id)

    return _write(run)


def cancel(request_id: int, actor: Actor | None = None) -> StockRequest:
    """The dealership withdraws an open request."""
    return _close(request_id, "cancelled", "cancelled", actor, None)


def decline(request_id: int, note: str | None = None, actor: Actor | None = None) -> StockRequest:
    """The depot turns an open request down, optionally saying why."""
    return _close(request_id, "declined", "declined", actor, note)


def mark_planned(conn: sqlite3.Connection, request_ids: list[int], shipment_id: int, dealership_code: str,
                 actor: Actor | None = None) -> None:
    """Inside the caller's transaction (shipment_repository.create): tie
    open requests to the shipment just planned. Each must be open and from
    the shipment's dealership, or the whole shipment is refused."""
    for request_id in dict.fromkeys(request_ids):
        current = _fetch(conn, request_id)
        if current.dealership_code != dealership_code:
            raise UserError("err.stock_request_other_dealership", number=current.number,
                            dealership=current.dealership_name)
        cursor = conn.execute(
            "UPDATE stock_requests SET status = 'planned', shipment_id = ?, decided_by = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? AND status = 'open'",
            (shipment_id, actor_label(actor), request_id),
        )
        if cursor.rowcount == 0:
            raise StockRequestStateError(current.number, current.status, "planned")


def reopen_for_shipment(conn: sqlite3.Connection, shipment_id: int) -> int:
    """Inside shipment_repository.cancel's transaction: the requests a
    cancelled shipment was meant to fill go back to "open" (unless the
    dealership asked again for the same product in the meantime - then the
    old one is closed as cancelled, so there is still one open request).
    Returns how many were reopened."""
    rows = conn.execute(
        "SELECT id, dealership_code, product_barcode FROM stock_requests WHERE shipment_id = ? AND status = 'planned'",
        (shipment_id,),
    ).fetchall()
    reopened = 0
    for row in rows:
        clash = conn.execute(
            "SELECT 1 FROM stock_requests WHERE dealership_code = ? AND product_barcode = ? COLLATE NOCASE "
            "AND status = 'open'",
            (row["dealership_code"], row["product_barcode"]),
        ).fetchone()
        new_status = "cancelled" if clash else "open"
        conn.execute(
            "UPDATE stock_requests SET status = ?, shipment_id = NULL, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (new_status, row["id"]),
        )
        reopened += new_status == "open"
    return reopened


def dealership_shortages(dealership_code: str | None = None) -> list[DealershipShortage]:
    """Products at/below their reorder level on an active dealership's
    shelf (same rule as stock_repository.critical_at: only products that
    shelf has carried, only with a reorder level set), with the units on
    active shipments to it and in its open requests. Most urgent first:
    nothing coming or asked for, then the emptiest shelf."""
    with connection_scope() as conn:
        sql = "SELECT code, name FROM dealerships WHERE is_active = 1"
        params: tuple = ()
        if dealership_code is not None:
            sql += " AND code = ?"
            params = (dealership_code,)
        shops = conn.execute(sql + " ORDER BY name", params).fetchall()
        incoming: dict[tuple[str, str], int] = {}
        for row in conn.execute(
            "SELECT s.dealership_code, UPPER(l.product_barcode) AS barcode, SUM(l.expected_qty) AS qty "
            "FROM shipments s JOIN shipment_lines l ON l.shipment_id = s.id "
            "WHERE s.status IN ('scheduled', 'in_transit') GROUP BY s.dealership_code, UPPER(l.product_barcode)"
        ):
            incoming[(row["dealership_code"], row["barcode"])] = int(row["qty"])
        requested: dict[tuple[str, str], int] = {}
        for row in conn.execute(
            "SELECT dealership_code, UPPER(product_barcode) AS barcode, SUM(quantity) AS qty FROM stock_requests "
            "WHERE status = 'open' GROUP BY dealership_code, UPPER(product_barcode)"
        ):
            requested[(row["dealership_code"], row["barcode"])] = int(row["qty"])

    result: list[DealershipShortage] = []
    for shop in shops:
        for product in critical_at(StockLocation.dealership(shop["code"])):
            key = (shop["code"], product.barcode.upper())
            result.append(DealershipShortage(
                dealership_code=shop["code"],
                dealership_name=shop["name"],
                product_barcode=product.barcode,
                product_name=product.name,
                on_hand=product.stock_quantity,
                reorder_level=product.critical_stock_level,
                incoming_qty=incoming.get(key, 0),
                requested_qty=requested.get(key, 0),
            ))
    result.sort(key=lambda s: (s.covered, s.on_hand - s.reorder_level, s.dealership_name, s.product_name))
    return result
