"""Sales in server mode, offline-first.

Every sale is first written to the local ``sale_outbox`` table (database/
migrations.py v11), so a sale the cashier has rung up is never lost, then sent
to the server (POST /sales, keyed by the sale's client_uuid):

- The server answers: the sale is marked sent and the receipt carries the
  server's sale number. A live refusal (a price moved, the shelf is short, the
  account changed) is shown to the cashier exactly as before, and the queued
  copy is dropped: nothing was sold.
- No answer (offline): the sale stays queued, the till's cached shelf goes
  down by the units sold, and the receipt prints without a number.
  ``send_pending()`` (run on a timer by POS) sends queued sales oldest first,
  with the time they were made; the server stores each once even if it is
  sent twice. One it refuses (the shelf really did not hold the units) is
  marked rejected and kept for a person to look at.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from database import remote
from database.connection import connection_scope
import re

from database.exceptions import InsufficientStockError, PriceChangedError, ProductNotFoundError
from shared.auth import Actor, actor_label
from shared.models import LineItem, StockLocation, Transaction


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _local(moment: datetime) -> datetime:
    """A naive LOCAL time, as transaction_repository returns created_at."""
    return moment.astimezone().replace(tzinfo=None)


def _payload(transaction: Transaction, location: StockLocation, client_uuid: str) -> dict:
    return {
        "client_uuid": client_uuid,
        "dealership_code": None if location.is_unassigned else location.code,
        "payment_method": transaction.payment_method,
        "lines": [{"barcode": i.product_barcode, "name": i.product_name_at_sale,
                   "unit_price": round(float(i.unit_price_at_sale), 2), "quantity": int(i.quantity)}
                  for i in transaction.items],
    }


def _from_server(data: dict) -> Transaction:
    created = datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))
    return Transaction(
        id=int(data["id"]), created_at=_local(created), dealership_code=data["dealership_code"],
        cashier=data["cashier"], payment_method=data["payment_method"], client_uuid=data["client_uuid"],
        items=[LineItem(product_barcode=ln["barcode"], product_name_at_sale=ln["name"],
                        unit_price_at_sale=float(ln["unit_price"]), quantity=int(ln["quantity"]),
                        unit_cost_at_sale=float(ln["unit_cost"]), cost_known=bool(ln["cost_known"]))
               for ln in data["lines"]])


def _queue(client_uuid: str, payload: dict) -> None:
    with connection_scope() as conn:
        conn.execute("INSERT OR IGNORE INTO sale_outbox (client_uuid, payload) VALUES (?, ?)",
                     (client_uuid, json.dumps(payload)))


def _mark(client_uuid: str, status: str, *, server_id: int | None = None, error: str | None = None) -> None:
    with connection_scope() as conn:
        conn.execute(
            "UPDATE sale_outbox SET status = ?, server_id = COALESCE(?, server_id), last_error = ?, "
            "attempts = attempts + 1, sent_at = CASE WHEN ? = 'sent' THEN strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
            "ELSE sent_at END WHERE client_uuid = ?", (status, server_id, error, status, client_uuid))


def _drop(client_uuid: str) -> None:
    with connection_scope() as conn:
        conn.execute("DELETE FROM sale_outbox WHERE client_uuid = ? AND status = 'pending'", (client_uuid,))


def _take_off_cached_shelf(location: StockLocation, wanted: dict[str, int]) -> None:
    path = "/stock/products-at?" + urlencode({"kind": location.kind, "code": location.code})

    def change(rows):
        for row in rows:
            sold = wanted.get(str(row["barcode"]).upper())
            if sold:
                row["quantity"] = max(0, int(row["quantity"]) - sold)
        return rows

    remote.cache_update(path, change)


def finalize(transaction: Transaction, location: StockLocation, cashier: Actor | None) -> Transaction:
    """finalize_transaction() in server mode; the caller has validated the lines."""
    client_uuid = (transaction.client_uuid or "").strip() or uuid.uuid4().hex
    payload = _payload(transaction, location, client_uuid)
    wanted: dict[str, int] = {}
    for item in transaction.items:
        wanted[item.product_barcode.upper()] = wanted.get(item.product_barcode.upper(), 0) + int(item.quantity)
    _queue(client_uuid, payload)
    try:
        sold = remote.call("POST", "/sales", payload,
                           errors={404: lambda: ProductNotFoundError(payload["lines"][0]["barcode"])})
    except remote.ServerUnavailableError:
        sold_at = _now_utc()
        with connection_scope() as conn:  # the time it was rung up travels with it
            conn.execute("UPDATE sale_outbox SET payload = ? WHERE client_uuid = ?",
                         (json.dumps({**payload, "sold_at": sold_at.isoformat()}), client_uuid))
        _take_off_cached_shelf(location, wanted)
        return dataclasses.replace(transaction, id=None, created_at=_local(sold_at),
                                   dealership_code=payload["dealership_code"], cashier=actor_label(cashier),
                                   client_uuid=client_uuid)
    except remote.ServerRefusedError as exc:
        _drop(client_uuid)  # refused live: nothing was sold
        raise _as_cart_error(exc, location) from None
    except Exception:
        _drop(client_uuid)
        raise
    _mark(client_uuid, "sent", server_id=int(sold["id"]))
    if not sold.get("duplicate"):
        _take_off_cached_shelf(location, wanted)  # so the offline shelf starts from the truth
    return _from_server(sold)


def _as_cart_error(exc: "remote.ServerRefusedError", location: StockLocation) -> Exception:
    """The server's 409 words, as the errors the POS cart already knows how to fix."""
    text = exc.args[0] if exc.args else ""
    prices = re.findall(r"([^\s:;]+): ([0-9.]+) -> ([0-9.]+)", text) if "Prices changed" in text else []
    if prices:
        return PriceChangedError([(b, float(old), float(new)) for b, old, new in prices])
    short = re.search(r"Only (\d+) units of (\S+) at .*?the sale needs (\d+)", text)
    if short:
        return InsufficientStockError(short.group(2), int(short.group(3)), int(short.group(1)), location.label)
    return exc


def send_pending(limit: int = 50) -> dict:
    """Send queued sales, oldest first. Stops at the first one that gets no
    answer (still offline). Returns {"sent": n, "rejected": n, "pending": n}."""
    sent = rejected = 0
    if not remote.active():
        return {"sent": 0, "rejected": 0, "pending": pending_count()}
    remote.retry_now()  # this runs in the background: really try the server, don't wait out the pause
    with connection_scope() as conn:
        rows = conn.execute("SELECT client_uuid, payload FROM sale_outbox WHERE status = 'pending' "
                            "ORDER BY created_at, rowid LIMIT ?", (int(limit),)).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        payload.setdefault("sold_at", _now_utc().isoformat())  # queued, so it is not a live sale any more
        try:
            sold = remote.call("POST", "/sales", payload)
        except remote.ServerUnavailableError:
            break
        except remote.ServerRefusedError as exc:
            if exc.status in (404, 409, 422):
                _mark(row["client_uuid"], "rejected", error=str(exc))
                rejected += 1
                continue
            break
        except Exception:  # signed out / account changed: try again after the next sign-in
            break
        _mark(row["client_uuid"], "sent", server_id=int(sold["id"]))
        sent += 1
    return {"sent": sent, "rejected": rejected, "pending": pending_count()}


def pending_count() -> int:
    with connection_scope() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM sale_outbox WHERE status = 'pending'").fetchone()[0])


def rejected_sales() -> list[dict]:
    """Queued sales the server refused, newest first: client_uuid, created_at, error, payload."""
    with connection_scope() as conn:
        rows = conn.execute("SELECT client_uuid, created_at, last_error, payload FROM sale_outbox "
                            "WHERE status = 'rejected' ORDER BY created_at DESC").fetchall()
    return [{"client_uuid": r[0], "created_at": r[1], "error": r[2], "payload": json.loads(r[3])} for r in rows]


def list_between(start: datetime, end: datetime, dealership_code: str | None, net: bool = True) -> list[Transaction]:
    """Sales in [start, end) (LOCAL times): the server's, plus this till's
    sales still waiting to be sent (id None). Oldest first, like the local list."""
    since = start.astimezone(timezone.utc).isoformat()
    until = end.astimezone(timezone.utc).isoformat()
    params = {"since": since, "until": until}
    if dealership_code:
        params["dealership_code"] = dealership_code
    if not net:
        params["net"] = "false"
    try:
        sales = [_from_server(s) for s in remote.call("GET", "/sales?" + urlencode(params))]
    except remote.ServerUnavailableError:
        sales = []
    with connection_scope() as conn:
        rows = conn.execute("SELECT client_uuid, created_at, payload FROM sale_outbox WHERE status = 'pending'"
                            ).fetchall()
    for row in rows:
        payload = json.loads(row["payload"])
        made = datetime.fromisoformat(payload.get("sold_at") or row["created_at"].replace("Z", "+00:00"))
        local = _local(made)
        if not (start <= local < end):
            continue
        if dealership_code and payload.get("dealership_code") != dealership_code:
            continue
        sales.append(Transaction(
            id=None, created_at=local, dealership_code=payload.get("dealership_code"),
            payment_method=payload.get("payment_method"), client_uuid=row["client_uuid"],
            items=[LineItem(ln["barcode"], ln["name"], float(ln["unit_price"]), int(ln["quantity"]))
                   for ln in payload["lines"]]))
    return sorted(sales, key=lambda t: t.created_at)

