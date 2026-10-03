"""All SQL for the Shipment / Distribution domain - `shipments` and
`shipment_lines`.

The workflow:
1. depot_app's Console > Shipments creates a shipment from its warehouse
   (`origin_code`) to a dealership (carrier, driver, ETA, product lines)
   - status "scheduled".
2. The depot dispatches it when the truck leaves - "in_transit",
   `departed_at` stamped. If it runs late, the depot updates the ETA
   (the originally promised one is kept as `planned_eta`).
3. The dealership's pos_app Receive Inventory screen checks each line
   off (accepting it, or reporting a different received quantity) and
   completes the receipt - "delivered".
4. admin_app's Distribution page tracks all of it.

**Stock** (per location - see database/stock_repository.py):
- Creating a shipment moves nothing; it's a plan.
- Dispatching takes every line off the origin warehouse's stock (fails
  with InsufficientStockError, writing nothing, if the warehouse doesn't
  have it). The goods are now on the truck: still counted in the
  company total, at no location (`stock_moved` = 1).
- Cancelling a dispatched shipment puts the goods back at the origin.
- Completing the receipt puts the RECEIVED quantities on the
  dealership's shelf. A difference from what was shipped is a
  discrepancy: a shortfall is written off (lost / damaged in transit), a
  surplus is added - both move the company total, logged as a
  'discrepancy' movement at the dealership naming the shipment.
- A shipment received without ever being dispatched (a scheduled one
  checked in directly) is taken off the origin at receipt time instead.
- Shipments planned before warehouses existed have no origin_code and
  draw from UNASSIGNED stock.
Every stock step is inside the same BEGIN IMMEDIATE transaction as the
status change it belongs to.
"""

from __future__ import annotations

import sqlite3

from database.connection import connection_scope
from database.exceptions import (
    DealershipNotFoundError,
    ProductNotFoundError,
    ShipmentNotFoundError,
    ShipmentStateError,
)
from database.stock_repository import change_level, level_in, log_movement, require_location
from shared.auth import Actor
from shared.formatting import now_db_timestamp, to_db_timestamp
from shared.models import UNASSIGNED, Shipment, ShipmentLine, StockLocation


def _row_to_shipment(row: sqlite3.Row, lines: list[ShipmentLine]) -> Shipment:
    return Shipment(
        id=row["id"],
        origin=row["origin"],
        dealership_code=row["dealership_code"],
        dealership_name=row["dealership_name"],
        carrier=row["carrier"],
        driver=row["driver"],
        status=row["status"],
        departed_at=row["departed_at"],
        planned_eta=row["planned_eta"],
        eta=row["eta"],
        delivered_at=row["delivered_at"],
        receipt_note=row["receipt_note"],
        created_at=row["created_at"],
        lines=lines,
        origin_code=row["origin_code"],
        stock_moved=bool(row["stock_moved"]),
    )


def origin_location(shipment: Shipment) -> StockLocation:
    """Where the shipment's goods come out of."""
    return StockLocation.warehouse(shipment.origin_code) if shipment.origin_code else UNASSIGNED


def _lines_for(conn: sqlite3.Connection, shipment_ids: list[int]) -> dict[int, list[ShipmentLine]]:
    result: dict[int, list[ShipmentLine]] = {sid: [] for sid in shipment_ids}
    if not shipment_ids:
        return result
    marks = ",".join("?" * len(shipment_ids))
    rows = conn.execute(
        f"SELECT * FROM shipment_lines WHERE shipment_id IN ({marks}) ORDER BY id", shipment_ids
    ).fetchall()
    for row in rows:
        result[row["shipment_id"]].append(
            ShipmentLine(
                id=row["id"],
                product_barcode=row["product_barcode"],
                product_name=row["product_name_at_ship"],
                expected_qty=row["expected_qty"],
                received_qty=row["received_qty"],
            )
        )
    return result


def _fetch(conn: sqlite3.Connection, shipment_id: int) -> Shipment:
    row = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    if row is None:
        raise ShipmentNotFoundError(shipment_id)
    return _row_to_shipment(row, _lines_for(conn, [shipment_id])[shipment_id])


def get(shipment_id: int) -> Shipment:
    with connection_scope() as conn:
        return _fetch(conn, shipment_id)


def list_shipments(
    statuses: tuple[str, ...] | None = None,
    dealership_code: str | None = None,
    origin: str | None = None,
    origin_code: str | None = None,
) -> list[Shipment]:
    """Shipments with their lines, soonest ETA first. `origin` filters on
    the site text, `origin_code` on the warehouse code."""
    clauses, params = [], []
    if statuses:
        clauses.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if dealership_code is not None:
        clauses.append("dealership_code = ?")
        params.append(dealership_code)
    if origin is not None:
        clauses.append("origin = ?")
        params.append(origin)
    if origin_code is not None:
        clauses.append("origin_code = ?")
        params.append(origin_code)
    sql = "SELECT * FROM shipments"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY eta, id"
    with connection_scope() as conn:
        rows = conn.execute(sql, params).fetchall()
        lines = _lines_for(conn, [row["id"] for row in rows])
    return [_row_to_shipment(row, lines[row["id"]]) for row in rows]


def create(
    origin: str,
    dealership_code: str,
    carrier: str,
    eta,
    lines: list[tuple[str, int]],
    driver: str | None = None,
    origin_code: str | None = None,
) -> Shipment:
    """Plan a shipment ("scheduled"). `origin` is the site label shown
    everywhere ("WH-01 · İstanbul Merkez"); `origin_code` the warehouse
    whose stock it will come out of (None: unassigned stock). `eta` is a
    datetime (naive = local time). `lines` is [(barcode, quantity), ...];
    the same product twice is merged. Raises ValueError for blank fields /
    no lines / a non-positive quantity, DealershipNotFoundError,
    ProductNotFoundError, UnknownLocationError for an unknown origin_code.
    Stock isn't checked here - it's taken (and checked) on dispatch."""
    origin, carrier = (origin or "").strip(), (carrier or "").strip()
    driver = (driver or "").strip() or None
    origin_code = (origin_code or "").strip() or None
    if not origin:
        raise ValueError("A shipment needs an origin.")
    if not carrier:
        raise ValueError("Enter a carrier.")
    merged: dict[str, int] = {}
    for barcode, quantity in lines:
        if int(quantity) <= 0:
            raise ValueError("Every line needs a quantity above 0.")
        merged[barcode] = merged.get(barcode, 0) + int(quantity)
    if not merged:
        raise ValueError("Add at least one product.")
    eta_text = to_db_timestamp(eta)

    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if origin_code:
                require_location(conn, StockLocation.warehouse(origin_code))
            dealership = conn.execute(
                "SELECT name FROM dealerships WHERE code = ?", (dealership_code,)
            ).fetchone()
            if dealership is None:
                raise DealershipNotFoundError(dealership_code)
            names = {}
            for barcode in merged:
                product = conn.execute("SELECT name FROM products WHERE barcode = ?", (barcode,)).fetchone()
                if product is None:
                    raise ProductNotFoundError(barcode)
                names[barcode] = product["name"]
            cursor = conn.execute(
                "INSERT INTO shipments (origin, origin_code, dealership_code, dealership_name, carrier, driver, "
                "planned_eta, eta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (origin, origin_code, dealership_code, dealership["name"], carrier, driver, eta_text, eta_text),
            )
            shipment_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO shipment_lines (shipment_id, product_barcode, product_name_at_ship, expected_qty) "
                "VALUES (?, ?, ?, ?)",
                [(shipment_id, barcode, names[barcode], qty) for barcode, qty in merged.items()],
            )
            shipment = _fetch(conn, shipment_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return shipment


def _write(shipment_id: int, fn) -> Shipment:
    """fn(conn, shipment) inside one BEGIN IMMEDIATE; returns the re-read shipment."""
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            fn(conn, _fetch(conn, shipment_id))
            shipment = _fetch(conn, shipment_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    return shipment


def _touch(conn: sqlite3.Connection, shipment_id: int, sql_set: str, params: tuple = ()) -> None:
    conn.execute(
        f"UPDATE shipments SET {sql_set}, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
        (*params, shipment_id),
    )


def dispatch(shipment_id: int, actor: Actor | None = None) -> Shipment:
    """The truck left: scheduled -> in_transit, departed_at = now, and
    every line comes off the origin warehouse's stock. Raises
    InsufficientStockError (nothing written) if the origin is short of
    any line, ShipmentStateError if it isn't scheduled."""

    def run(conn, shipment):
        if shipment.status != "scheduled":
            raise ShipmentStateError(shipment.number, shipment.status, "dispatched")
        origin = origin_location(shipment)
        for line in shipment.lines:
            change_level(conn, origin, line.product_barcode, -line.expected_qty, change_total=False)
            log_movement(conn, origin, line.product_barcode, "dispatch", line.expected_qty, reason="shipment",
                         reference=shipment.number, note=f"Loaded for {shipment.dealership_name}", actor=actor)
        _touch(conn, shipment.id, "status = 'in_transit', departed_at = ?, stock_moved = 1", (now_db_timestamp(),))

    return _write(shipment_id, run)


def update_eta(shipment_id: int, eta) -> Shipment:
    """New arrival estimate for a shipment that hasn't arrived. The
    original promise stays in planned_eta, so the lateness is visible."""
    eta_text = to_db_timestamp(eta)

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "re-timed")
        _touch(conn, shipment.id, "eta = ?", (eta_text,))

    return _write(shipment_id, run)


def cancel(shipment_id: int, actor: Actor | None = None) -> Shipment:
    """Call the shipment off. If it was already dispatched, the goods go
    back onto the origin warehouse's stock."""

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "cancelled")
        if shipment.stock_moved:
            origin = origin_location(shipment)
            for line in shipment.lines:
                change_level(conn, origin, line.product_barcode, line.expected_qty, change_total=False)
                log_movement(conn, origin, line.product_barcode, "receive", line.expected_qty, reason="shipment",
                             reference=shipment.number, note="Returned - shipment cancelled", actor=actor)
        _touch(conn, shipment.id, "status = 'cancelled', stock_moved = 0")

    return _write(shipment_id, run)


def complete_receipt(shipment_id: int, received: dict[str, int], note: str | None = None,
                     actor: Actor | None = None) -> Shipment:
    """The dealership checked the shipment in. `received` maps barcode ->
    units actually received, for every line (a line left out counts as
    received in full). Marks it delivered and puts the received units on
    the dealership's shelf; see the module docstring for discrepancies.

    One BEGIN IMMEDIATE transaction, with the status check inside it, so
    two terminals completing the same receipt can't both move stock: the
    second gets ShipmentStateError.
    """
    note = (note or "").strip() or None
    for barcode, quantity in received.items():
        if int(quantity) < 0:
            raise ValueError("Received quantities can't be negative.")

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "received")
        unknown = set(received) - {line.product_barcode for line in shipment.lines}
        if unknown:
            raise ValueError(f"{', '.join(sorted(unknown))} isn't on {shipment.number}.")

        origin = origin_location(shipment)
        shelf = StockLocation.dealership(shipment.dealership_code)
        for line in shipment.lines:
            barcode = line.product_barcode
            got = int(received.get(barcode, line.expected_qty))
            conn.execute("UPDATE shipment_lines SET received_qty = ? WHERE id = ?", (got, line.id))

            on_truck = line.expected_qty
            short_note = None
            if not shipment.stock_moved:
                # Never dispatched: take it off the origin now, as far as
                # the origin's records go.
                on_truck = min(line.expected_qty, level_in(conn, origin, barcode))
                if on_truck:
                    change_level(conn, origin, barcode, -on_truck, change_total=False)
                    log_movement(conn, origin, barcode, "dispatch", on_truck, reason="shipment",
                                 reference=shipment.number, note=f"Loaded for {shipment.dealership_name}", actor=actor)
                if on_truck < line.expected_qty:
                    short_note = f"{origin.label} had only {on_truck} of {line.expected_qty} on record"

            if on_truck:
                change_level(conn, shelf, barcode, on_truck, change_total=False)
                log_movement(conn, shelf, barcode, "receive", on_truck, reason="shipment",
                             reference=shipment.number, note=f"From {shipment.origin}", actor=actor)
            diff = got - on_truck
            if diff:
                change_level(conn, shelf, barcode, diff, change_total=True)
                if diff < 0:
                    text = f"Short {-diff} of {line.expected_qty} shipped"
                else:
                    text = f"Over by {diff} ({got} received, {line.expected_qty} shipped)"
                if short_note:
                    text += f" - {short_note}"
                log_movement(conn, shelf, barcode, "dispatch" if diff < 0 else "receive", abs(diff),
                             reason="discrepancy", reference=shipment.number, note=text, actor=actor)
        now = now_db_timestamp()
        _touch(conn, shipment.id,
               "status = 'delivered', delivered_at = ?, receipt_note = ?, departed_at = COALESCE(departed_at, ?), "
               "stock_moved = 0",
               (now, note, now))

    return _write(shipment_id, run)


def count_incoming(dealership_code: str | None) -> int:
    """Active (scheduled / in transit) shipments for a dealership - the
    POS home tile's "N arriving" badge. None counts every dealership."""
    sql = "SELECT COUNT(*) FROM shipments WHERE status IN ('scheduled', 'in_transit')"
    params: tuple = ()
    if dealership_code is not None:
        sql += " AND dealership_code = ?"
        params = (dealership_code,)
    with connection_scope() as conn:
        return int(conn.execute(sql, params).fetchone()[0])
