"""Server versions of the refund and end-of-day repository functions.

sale_return_repository, day_close_repository and transaction_repository.get_by_id call these instead of
SQLite when database.remote is active. Same signatures and return types as the local functions. The
server checks the rules (a manager approves with their own PIN, quantities against sold minus returned,
one stored refund per client_uuid, the day's arithmetic) and its refusals come back with its own words -
the common ones as the same exceptions the screens already catch."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from database import remote
from database.exceptions import ReturnApprovalError, ReturnQuantityError, TransactionNotFoundError
from database.remote_sales import _from_server, _local
from shared.auth import Actor
from shared.models import DayClose, DaySummary, ReturnableLine, ReturnLine, SaleReturn


def _call(method: str, url: str, body: dict | None = None, *, errors: dict | None = None):
    try:
        return remote.call(method, url, body, errors=errors)
    except remote.ServerRefusedError as exc:
        text = str(exc)
        if exc.status == 403 and "manager must approve" in text.lower():
            raise ReturnApprovalError() from None
        if exc.status == 409:
            match = re.search(r"Only (\d+) of (.+) can still come back; (\d+) were asked for", text)
            if match:
                raise ReturnQuantityError(match.group(2), int(match.group(3)), int(match.group(1))) from None
        raise


def _utc(moment: datetime) -> str:
    return moment.astimezone().isoformat()  # a naive local time is read as local


def sale(transaction_id: int):
    return _from_server(_call("GET", remote.path("sales", str(transaction_id)),
                              errors={404: lambda: TransactionNotFoundError(transaction_id)}))


def returnable_lines(transaction_id: int) -> list[ReturnableLine]:
    rows = _call("GET", remote.path("returns", "returnable", str(transaction_id)),
                 errors={404: lambda: TransactionNotFoundError(transaction_id)})
    return [ReturnableLine(r["barcode"], r["name"], float(r["unit_price"]), int(r["sold"]), int(r["returned"]))
            for r in rows]


def _sale_return(data: dict) -> SaleReturn:
    return SaleReturn(
        id=int(data["id"]), transaction_id=int(data["transaction_id"]), reason=data["reason"],
        created_at=_local(datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))),
        dealership_code=data.get("dealership_code"), payment_method=data.get("payment_method"),
        requested_by=data.get("requested_by"), approved_by=data.get("approved_by"), client_uuid=data.get("client_uuid"),
        lines=[ReturnLine(l["barcode"], l["name"], float(l["unit_price"]), int(l["quantity"]), bool(l["restock"]))
               for l in data.get("lines", [])])


def create(transaction_id: int, lines: list[tuple[str, int, bool]], reason: str, approver_badge: str,
           approver_pin: str, terminal: str, client_uuid: str) -> SaleReturn:
    body = {"transaction_id": int(transaction_id), "reason": reason, "client_uuid": client_uuid,
            "approver_badge": approver_badge.strip(), "approver_pin": approver_pin, "terminal": terminal,
            "lines": [{"barcode": b, "quantity": int(q), "restock": bool(r)} for b, q, r in lines]}
    return _sale_return(_call("POST", "/returns", body, errors={404: lambda: TransactionNotFoundError(transaction_id)}))


def get(return_id: int) -> SaleReturn:
    return _sale_return(_call("GET", remote.path("returns", str(return_id)),
                              errors={404: lambda: TransactionNotFoundError(return_id)}))


def list_for_transaction(transaction_id: int) -> list[SaleReturn]:
    return [_sale_return(r) for r in _call("GET", "/returns?" + urlencode({"transaction_id": transaction_id}))]


def list_between(start: datetime, end: datetime, dealership_code: str | None) -> list[SaleReturn]:
    query = {"since": _utc(start), "until": _utc(end)}
    if dealership_code is not None:
        query["dealership_code"] = dealership_code
    return [_sale_return(r) for r in _call("GET", "/returns?" + urlencode(query))]


# --- end of day ---------------------------------------------------------------------------

def _bounds(day: date) -> tuple[datetime, datetime]:
    from datetime import time

    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def summarize(dealership_code: str | None, day: date) -> DaySummary:
    start, end = _bounds(day)
    query = {"start": _utc(start), "end": _utc(end)}
    if dealership_code:
        query["dealership_code"] = dealership_code
    got = _call("GET", "/day-close/summary?" + urlencode(query))
    return DaySummary(
        dealership_code=dealership_code, business_date=day, sales_count=int(got["sales_count"]),
        cash_sales=got["cash_sales"], card_sales=got["card_sales"], other_sales=got["other_sales"],
        cash_refunds=got["cash_refunds"], card_refunds=got["card_refunds"], refunds_count=int(got["refunds_count"]))


def _close(data: dict) -> DayClose:
    return DayClose(
        id=int(data["id"]), created_at=_local(datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00"))),
        dealership_code=data.get("dealership_code"), business_date=date.fromisoformat(data["business_date"]),
        sales_count=int(data["sales_count"]), cash_sales=data["cash_sales"], card_sales=data["card_sales"],
        other_sales=data["other_sales"], cash_refunds=data["cash_refunds"], card_refunds=data["card_refunds"],
        expected_cash=data["expected_cash"], counted_cash=data["counted_cash"], difference=data["difference"],
        note=data.get("note"), closed_by=data.get("closed_by"))


def close(dealership_code: str | None, counted: float, day: date, note: str | None) -> DayClose:
    start, end = _bounds(day)
    return _close(_call("POST", "/day-close", {
        "dealership_code": dealership_code, "business_date": day.isoformat(), "start": _utc(start), "end": _utc(end),
        "counted_cash": counted, "note": note}))


def list_recent(limit: int, dealership_code: str | None, only_differences: bool) -> list[DayClose]:
    query: dict = {"limit": int(limit), "only_differences": "true" if only_differences else "false"}
    if dealership_code is not None:
        query["dealership_code"] = dealership_code
    return [_close(r) for r in _call("GET", "/day-closes?" + urlencode(query))]


def latest_for(dealership_code: str | None, day: date) -> DayClose | None:
    query = {"business_date": day.isoformat()}
    if dealership_code:
        query["dealership_code"] = dealership_code
    got = _call("GET", "/day-closes/latest?" + urlencode(query))
    return _close(got) if got else None
