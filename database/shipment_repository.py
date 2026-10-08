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
- Completing the receipt puts the units on the dealership's shelf. Only
  a SHORTFALL is a discrepancy: the units that never arrived are written
  off (lost / damaged in transit), moving the company total and logged as
  a 'discrepancy' movement at the dealership naming the shipment. You
  can't receive MORE than was shipped (ValueError) - that would create
  stock from nothing; a surplus is a supplier/count matter.
- A shipment that was never dispatched can't be received
  (ShipmentNotDispatchedError): the depot must dispatch it first, so the
  goods have really left the origin and the company total is conserved.
- Shipments planned before warehouses existed have no origin_code and
  draw from UNASSIGNED stock.
Every stock step is inside the same BEGIN IMMEDIATE transaction as the
status change it belongs to.
"""

from __future__ import annotations

import sqlite3

from shared.i18n import UserError
from database import activity_repository, stock_request_repository
from database.connection import connection_scope
from datetime import datetime, timedelta

from database.exceptions import (
    DealershipNotFoundError,
    ShipmentNotDispatchedError,
    ShipmentNotFoundError,
    ShipmentStateError,
)
from database.stock_repository import change_level, log_movement, require_location, resolve_product
from shared.auth import Actor
from shared.formatting import now_db_timestamp, parse_db_timestamp, to_db_timestamp
from shared.models import UNASSIGNED, Shipment, ShipmentLine, StockLocation
from shared.warehousing import whole_number

# An ETA this far before "now" (or before the departure) is still accepted:
# a form's "now" is a few seconds old by the time it is saved.
_ETA_GRACE = timedelta(minutes=5)


def _utc(value) -> datetime:
    """`value` (a datetime, naive = local, or a db timestamp string) as an aware UTC datetime."""
    return parse_db_timestamp(value if isinstance(value, str) else to_db_timestamp(value))


def _check_eta(eta, earliest, what: str) -> None:
    """ValueError unless `eta` is not before `earliest` (a datetime or db text, None = no limit)."""
    if earliest is not None and _utc(eta) < _utc(earliest) - _ETA_GRACE:
        raise UserError("err.eta_before", what=what)


def active_count_for(conn: sqlite3.Connection, *, origin_code: str | None = None,
                     dealership_code: str | None = None) -> int:
    """Scheduled / in-transit shipments leaving from warehouse `origin_code`
    or going to dealership `dealership_code` - what stops a location being
    deleted. Takes the caller's connection (it runs inside their transaction)."""
    if origin_code is not None:
        sql, param = "SELECT COUNT(*) FROM shipments WHERE status IN ('scheduled', 'in_transit') AND origin_code = ?", origin_code
    else:
        sql, param = "SELECT COUNT(*) FROM shipments WHERE status IN ('scheduled', 'in_transit') AND dealership_code = ?", dealership_code
    return int(conn.execute(sql, (param,)).fetchone()[0])


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
    departure=None,
    request_ids: list[int] | None = None,
    actor: Actor | None = None,
) -> Shipment:
    """Plan a shipment ("scheduled"). `origin` is the site label shown
    everywhere ("WH-01 · İstanbul Merkez"); `origin_code` the warehouse
    whose stock it will come out of (None: unassigned stock). `eta` is a
    datetime (naive = local time) and can't be in the past or before
    `departure` (the planned departure time, if known). `lines` is
    [(barcode, quantity), ...]; quantities must be whole numbers above 0
    (2.7 is an error, not 2); the same product twice is merged. Raises
    ValueError for blank fields / no lines / a bad quantity / a bad ETA,
    DealershipNotFoundError, ProductNotFoundError (ProductInactiveError for
    a deactivated product), UnknownLocationError for an unknown origin_code.
    Stock isn't checked here - it's taken (and checked) on dispatch.

    `request_ids`: the dealership's open stock requests this shipment
    fills - marked "planned" in the same transaction (see
    stock_request_repository.mark_planned); one that is no longer open or
    belongs to another dealership refuses the whole shipment."""
    origin, carrier = (origin or "").strip(), (carrier or "").strip()
    driver = (driver or "").strip() or None
    origin_code = (origin_code or "").strip() or None
    if not origin:
        raise UserError("err.shipment_origin")
    if not carrier:
        raise UserError("err.carrier_required")
    merged: dict[str, int] = {}
    for barcode, quantity in lines:
        quantity = whole_number(quantity, "Every line's quantity")
        if quantity <= 0:
            raise UserError("err.line_qty")
        key = str(barcode).strip()
        merged[key] = merged.get(key, 0) + quantity
    if not merged:
        raise UserError("err.add_product")
    eta_text = to_db_timestamp(eta)
    _check_eta(eta, departure if departure is not None else datetime.now().astimezone(),
               "the departure" if departure is not None else "now")

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
            canonical: dict[str, int] = {}
            for barcode, qty in merged.items():
                stored, name = resolve_product(conn, barcode, active_only=True)
                names[stored] = name
                canonical[stored] = canonical.get(stored, 0) + qty
            merged = canonical
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
            if request_ids:
                stock_request_repository.mark_planned(conn, list(request_ids), shipment_id, dealership_code, actor)
            shipment = _fetch(conn, shipment_id)
            activity_repository.record(
                conn, "shipment_created", source="depot", location=StockLocation.dealership(dealership_code),
                actor=actor, number=shipment.number, origin=origin, units=sum(merged.values()),
                lines=len(merged), carrier=carrier)
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
        require_location(conn, origin)
        for line in shipment.lines:
            left = change_level(conn, origin, line.product_barcode, -line.expected_qty, change_total=False)
            log_movement(conn, origin, line.product_barcode, "dispatch", line.expected_qty, reason="shipment",
                         reference=shipment.number, note=f"Loaded for {shipment.dealership_name}", actor=actor)
            activity_repository.stock_crossing(conn, origin, line.product_barcode, left + line.expected_qty, left,
                                               source="depot", actor=actor)
        _touch(conn, shipment.id, "status = 'in_transit', departed_at = ?, stock_moved = 1", (now_db_timestamp(),))
        activity_repository.record(
            conn, "shipment_dispatched", source="depot", location=StockLocation.dealership(shipment.dealership_code),
            actor=actor, number=shipment.number, origin=shipment.origin,
            units=sum(l.expected_qty for l in shipment.lines))

    return _write(shipment_id, run)


def update_eta(shipment_id: int, eta) -> Shipment:
    """New arrival estimate for a shipment that hasn't arrived. The
    original promise stays in planned_eta, so the lateness is visible. The
    new ETA can't be before the shipment was planned or, once it left,
    before it departed (ValueError)."""
    eta_text = to_db_timestamp(eta)

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "re-timed")
        if shipment.departed_at:
            _check_eta(eta, shipment.departed_at, "the departure")
        else:
            _check_eta(eta, shipment.created_at, "when the shipment was planned")
        _touch(conn, shipment.id, "eta = ?", (eta_text,))

    return _write(shipment_id, run)


def cancel(shipment_id: int, actor: Actor | None = None) -> Shipment:
    """Call the shipment off. If it was already dispatched, the goods go
    back onto the origin warehouse's stock. Stock requests it was planned
    from go back to "open" - the dealership still needs the goods."""

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "cancelled")
        if shipment.stock_moved:
            origin = origin_location(shipment)
            require_location(conn, origin)  # goods can't go back to a deleted warehouse
            for line in shipment.lines:
                change_level(conn, origin, line.product_barcode, line.expected_qty, change_total=False)
                log_movement(conn, origin, line.product_barcode, "receive", line.expected_qty, reason="shipment",
                             reference=shipment.number, note="Returned - shipment cancelled", actor=actor)
        _touch(conn, shipment.id, "status = 'cancelled', stock_moved = 0")
        stock_request_repository.reopen_for_shipment(conn, shipment.id)  # the dealership still needs it
        activity_repository.record(
            conn, "shipment_cancelled", severity="notice", source="depot",
            location=StockLocation.dealership(shipment.dealership_code), actor=actor, number=shipment.number)

    return _write(shipment_id, run)


def complete_receipt(shipment_id: int, received: dict[str, int], note: str | None = None,
                     actor: Actor | None = None) -> Shipment:
    """The dealership checked the shipment in. `received` maps barcode ->
    units actually received, for every line (a line left out counts as
    received in full). Marks it delivered and puts the received units on
    the dealership's shelf; see the module docstring for discrepancies.

    Raises ValueError for a negative or non-whole quantity, or MORE than
    the line's expected quantity (nothing is written); ShipmentNotDispatchedError
    for a shipment that never left the origin; ShipmentStateError if it is
    already delivered / cancelled.

    One BEGIN IMMEDIATE transaction, with the status check inside it, so
    two terminals completing the same receipt can't both move stock: the
    second gets ShipmentStateError.
    """
    note = (note or "").strip() or None
    counts: dict[str, int] = {}
    for barcode, quantity in received.items():
        quantity = whole_number(quantity, "Received quantities")
        if quantity < 0:
            raise UserError("err.received_negative")
        counts[str(barcode).strip().upper()] = quantity

    def run(conn, shipment):
        if not shipment.is_active:
            raise ShipmentStateError(shipment.number, shipment.status, "received")
        if shipment.status != "in_transit" or not shipment.stock_moved:
            raise ShipmentNotDispatchedError(shipment.number, shipment.status)
        expected = {line.product_barcode.upper(): line for line in shipment.lines}
        unknown = set(counts) - set(expected)
        if unknown:
            raise UserError("err.not_on_shipment", names=', '.join(sorted(unknown)), number=shipment.number)
        for key, count in counts.items():
            if count > expected[key].expected_qty:
                raise UserError(
                    "err.over_receive", product=expected[key].product_name, count=count,
                    expected=expected[key].expected_qty, number=shipment.number,
                )

        shelf = StockLocation.dealership(shipment.dealership_code)
        require_location(conn, shelf)  # the dealership still exists (it can't be deleted mid-shipment)
        for line in shipment.lines:
            barcode = line.product_barcode
            got = counts.get(barcode.upper(), line.expected_qty)
            conn.execute("UPDATE shipment_lines SET received_qty = ? WHERE id = ?", (got, line.id))

            # The whole shipped quantity arrives on the shelf (it is on the
            # truck, counted in the company total), then anything short is
            # written off as lost / damaged in transit.
            change_level(conn, shelf, barcode, line.expected_qty, change_total=False)
            log_movement(conn, shelf, barcode, "receive", line.expected_qty, reason="shipment",
                         reference=shipment.number, note=f"From {shipment.origin}", actor=actor)
            short = line.expected_qty - got
            if short:
                change_level(conn, shelf, barcode, -short, change_total=True)
                log_movement(conn, shelf, barcode, "dispatch", short, reason="discrepancy",
                             reference=shipment.number,
                             note=f"Short {short} of {line.expected_qty} shipped", actor=actor)
        now = now_db_timestamp()
        _touch(conn, shipment.id,
               "status = 'delivered', delivered_at = ?, receipt_note = ?, departed_at = COALESCE(departed_at, ?), "
               "stock_moved = 0",
               (now, note, now))
        missing = sum(l.expected_qty - counts.get(l.product_barcode.upper(), l.expected_qty) for l in shipment.lines)
        activity_repository.record(
            conn, "shipment_short" if missing else "shipment_delivered", severity="warning" if missing else "info",
            source="pos", location=shelf, actor=actor, number=shipment.number, origin=shipment.origin,
            units=sum(l.expected_qty for l in shipment.lines) - missing, missing=missing, note=note or "")

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
