"""End-of-day till count (`day_closes`, migration v9).

summarize() adds up one dealership's calendar day (LOCAL time, as the sales
reports do): sales by payment method straight from the sales (not net of
refunds), and the refunds paid out THAT day - so the drawer arithmetic holds
even when today's refund is for last week's sale. The cash the drawer should
hold is cash sales less cash refunds. close() stores that against the
cashier's count; the difference (counted - expected) is what Admin looks at.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from database import sale_return_repository, transaction_repository
from database.connection import connection_scope
from shared.auth import Actor, actor_label
from shared.i18n import UserError
from shared.models import DayClose, DaySummary

MAX_NOTE_LENGTH = 200


def _bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def summarize(dealership_code: str | None, day: date | None = None) -> DaySummary:
    day = day or date.today()
    start, end = _bounds(day)
    sales = [t for t in transaction_repository.list_between(start, end, net_of_returns=False)
             if t.dealership_code == dealership_code]
    refunds = [r for r in sale_return_repository.list_between(start, end) if r.dealership_code == dealership_code]

    def by_method(rows, method):
        return round(sum(r.total for r in rows if r.payment_method == method), 2)

    return DaySummary(
        dealership_code=dealership_code, business_date=day, sales_count=len(sales),
        cash_sales=by_method(sales, "cash"), card_sales=by_method(sales, "card"),
        other_sales=round(sum(t.total for t in sales if t.payment_method not in ("cash", "card")), 2),
        cash_refunds=by_method(refunds, "cash"), card_refunds=by_method(refunds, "card"),
        refunds_count=len(refunds),
    )


def close(dealership_code: str | None, counted_cash: float, day: date | None = None, actor: Actor | None = None,
          note: str | None = None) -> DayClose:
    """Store the count for `day` (default today) against what the day's sales say.
    `counted_cash` must be a number >= 0; a note longer than MAX_NOTE_LENGTH is refused."""
    try:
        counted = round(float(counted_cash), 2)
    except (TypeError, ValueError):
        raise UserError("err.close_counted") from None
    if counted != counted or counted < 0 or counted > 100_000_000:
        raise UserError("err.close_counted")
    note = (note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise UserError("err.close_note_long", n=MAX_NOTE_LENGTH)
    summary = summarize(dealership_code, day)
    expected = summary.expected_cash
    with connection_scope() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "INSERT INTO day_closes (dealership_code, business_date, sales_count, cash_sales, card_sales, "
                "other_sales, cash_refunds, card_refunds, expected_cash, counted_cash, difference, note, closed_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (dealership_code, summary.business_date.isoformat(), summary.sales_count, summary.cash_sales,
                 summary.card_sales, summary.other_sales, summary.cash_refunds, summary.card_refunds, expected,
                 counted, round(counted - expected, 2), note, actor_label(actor)),
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        row = conn.execute("SELECT * FROM day_closes WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row(row)


def list_recent(limit: int = 60, dealership_code: str | None = None, only_differences: bool = False) -> list[DayClose]:
    sql, params = "SELECT * FROM day_closes WHERE 1 = 1", []
    if dealership_code is not None:
        sql += " AND dealership_code = ?"
        params.append(dealership_code)
    if only_differences:
        sql += " AND difference <> 0"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with connection_scope() as conn:
        return [_row(r) for r in conn.execute(sql, params).fetchall()]


def latest_for(dealership_code: str | None, day: date) -> DayClose | None:
    with connection_scope() as conn:
        row = conn.execute(
            "SELECT * FROM day_closes WHERE business_date = ? AND dealership_code IS ? ORDER BY id DESC LIMIT 1",
            (day.isoformat(), dealership_code),
        ).fetchone()
    return _row(row) if row else None


def _row(row) -> DayClose:
    created = transaction_repository._as_local(transaction_repository._parse_timestamp(row["created_at"]))
    return DayClose(
        id=row["id"], created_at=created, dealership_code=row["dealership_code"],
        business_date=date.fromisoformat(row["business_date"]), sales_count=row["sales_count"],
        cash_sales=row["cash_sales"], card_sales=row["card_sales"], other_sales=row["other_sales"],
        cash_refunds=row["cash_refunds"], card_refunds=row["card_refunds"], expected_cash=row["expected_cash"],
        counted_cash=row["counted_cash"], difference=row["difference"], note=row["note"], closed_by=row["closed_by"],
    )
