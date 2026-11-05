"""Refunds against earlier sales and the end-of-day till count.

Same rules as the desktop's sale_return_repository.py and day_close_repository.py:

- A sale is never edited. A refund names the sale, the products and quantities coming back, why,
  and who approved it. Units marked ``restock`` go back on the sale's dealership shelf (and the
  company total); damaged ones only move money. The money goes back the way it came.
- A manager (administrator or depot manager) must approve with their own badge and PIN. The PIN is
  checked here with the normal sign-in lockout and logged as ``pin_confirmed``, and the approver's
  account is checked again inside the refund's own transaction.
- Quantities are checked against sold minus already returned, so the same units can never be
  refunded twice, even from two tills at once. ``client_uuid`` makes a re-sent refund store once.
- The day count adds up one dealership's day (the client sends the day's bounds, because "a day" is
  the till's local day): sales by payment method straight from the sales, and the refunds paid out
  THAT day. Expected cash is cash sales less cash refunds; the difference is counted - expected.

Who: cashiers and administrators at a POS refund and count the drawer; everyone who may read sales
may read refunds and day counts.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from . import activity, authz, db
from .auth_routes import Session, check_credentials, current_session
from .sale_routes import POS_AREA, READERS, SELLERS, SaleOut, _read_sale
from .stock_routes import _change, _fail, _write

router = APIRouter(tags=["returns"])

APPROVER_ROLES = frozenset({"admin", "depot_manager"})
MAX_REASON = 200
MAX_NOTE = 200
_CENT = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def _readers(session: Session) -> None:
    if session.role not in READERS:
        raise _fail(403, "Not allowed.")


# --- reading one sale ---------------------------------------------------------------------

@router.get("/sales/{sale_id}", response_model=SaleOut)
def get_sale(sale_id: int, request: Request, session: Session = Depends(current_session)) -> SaleOut:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        found = conn.execute("SELECT dealership_code FROM transactions WHERE id = %s", (sale_id,)).fetchone()
        if found is None or not authz.same_shop(conn, session, found["dealership_code"]):
            raise _fail(404, f"No sale with id {sale_id}.")
        return _read_sale(conn, sale_id)


# --- refunds ------------------------------------------------------------------------------

class ReturnLineIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=1_000_000)
    restock: bool = True


class ReturnIn(BaseModel):
    transaction_id: int = Field(gt=0, le=2_147_483_647)
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=500)
    reason: str = Field(max_length=1000)
    client_uuid: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    approver_badge: str = Field(max_length=64)
    approver_pin: str = Field(max_length=64)
    terminal: str = Field(default="", max_length=128)


class ReturnLineOut(BaseModel):
    barcode: str
    name: str
    unit_price: float
    quantity: int
    restock: bool


class ReturnOut(BaseModel):
    id: int
    transaction_id: int
    created_at: datetime
    dealership_code: str | None
    payment_method: str | None
    reason: str
    requested_by: str | None
    approved_by: str | None
    client_uuid: str | None
    total: float
    lines: list[ReturnLineOut]


class ReturnableOut(BaseModel):
    barcode: str
    name: str
    unit_price: float
    sold: int
    returned: int


def _sold(conn, sale_id: int) -> dict[str, tuple[str, Decimal, int]]:
    """barcode -> (name, unit price of its first line, total quantity)."""
    lines: dict[str, tuple[str, Decimal, int]] = {}
    for row in conn.execute("SELECT product_barcode, product_name_at_sale, unit_price_at_sale, quantity "
                            "FROM transaction_items WHERE transaction_id = %s ORDER BY id", (sale_id,)).fetchall():
        name, price, qty = lines.get(row["product_barcode"],
                                     (row["product_name_at_sale"], row["unit_price_at_sale"], 0))
        lines[row["product_barcode"]] = (name, price, qty + row["quantity"])
    return lines


def _returned(conn, sale_id: int) -> dict[str, int]:
    return {r["product_barcode"]: int(r["n"]) for r in conn.execute(
        "SELECT i.product_barcode, SUM(i.quantity) AS n FROM sale_return_items i "
        "JOIN sale_returns r ON r.id = i.return_id WHERE r.transaction_id = %s GROUP BY i.product_barcode",
        (sale_id,)).fetchall()}


def _returns(conn, where: str, params: tuple) -> list[ReturnOut]:
    heads = conn.execute("SELECT * FROM sale_returns " + where + " ORDER BY id", params).fetchall()
    if not heads:
        return []
    items: dict[int, list[ReturnLineOut]] = {h["id"]: [] for h in heads}
    for i in conn.execute("SELECT * FROM sale_return_items WHERE return_id = ANY(%s) ORDER BY id",
                          ([h["id"] for h in heads],)).fetchall():
        items[i["return_id"]].append(ReturnLineOut(
            barcode=i["product_barcode"], name=i["product_name_at_sale"], unit_price=float(i["unit_price_at_sale"]),
            quantity=i["quantity"], restock=i["restock"]))
    return [ReturnOut(
        id=h["id"], transaction_id=h["transaction_id"], created_at=h["created_at"],
        dealership_code=h["dealership_code"], payment_method=h["payment_method"], reason=h["reason"],
        requested_by=h["requested_by"], approved_by=h["approved_by"], client_uuid=h["client_uuid"],
        total=float(h["total"]), lines=items[h["id"]]) for h in heads]


@router.get("/returns/returnable/{sale_id}", response_model=list[ReturnableOut])
def returnable(sale_id: int, request: Request, session: Session = Depends(current_session)) -> list[ReturnableOut]:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        found = conn.execute("SELECT dealership_code FROM transactions WHERE id = %s", (sale_id,)).fetchone()
        if found is None or not authz.same_shop(conn, session, found["dealership_code"]):
            raise _fail(404, f"No sale with id {sale_id}.")
        returned = _returned(conn, sale_id)
        return [ReturnableOut(barcode=b, name=n, unit_price=float(p), sold=q, returned=returned.get(b, 0))
                for b, (n, p, q) in _sold(conn, sale_id).items()]


@router.get("/returns", response_model=list[ReturnOut])
def list_returns(request: Request, since: datetime | None = None, until: datetime | None = None,
                 transaction_id: int | None = None, dealership_code: str | None = None,
                 session: Session = Depends(current_session)) -> list[ReturnOut]:
    """Refunds of one sale, or paid out in [since, until)."""
    _readers(session)
    if transaction_id is None and (since is None or until is None):
        raise _fail(422, "Give a transaction_id, or both since and until.")
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        shop = authz.scope_shop(conn, session, dealership_code)
        if transaction_id is not None:
            found = _returns(conn, "WHERE transaction_id = %s", (transaction_id,))
            return [r for r in found if authz.same_shop(conn, session, r.dealership_code)]
        return _returns(conn, "WHERE created_at >= %s AND created_at < %s AND (%s::text IS NULL OR dealership_code = %s)",
                        (since, until, shop, shop))


@router.get("/returns/{return_id}", response_model=ReturnOut)
def get_return(return_id: int, request: Request, session: Session = Depends(current_session)) -> ReturnOut:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        found = _returns(conn, "WHERE id = %s", (return_id,))
        if not found or not authz.same_shop(conn, session, found[0].dealership_code):
            raise _fail(404, f"No refund with id {return_id}.")
        return found[0]


@router.post("/returns", response_model=ReturnOut)
def create_return(body: ReturnIn, request: Request, session: Session = Depends(current_session)) -> ReturnOut:
    authz.require(session, roles=SELLERS, areas=POS_AREA)
    reason = (body.reason or "").strip()
    if not reason:
        raise _fail(422, "Say why the goods are coming back.")
    if len(reason) > MAX_REASON:
        raise _fail(422, f"The reason can have at most {MAX_REASON} characters.")
    wanted: dict[str, list[int]] = {}  # barcode -> [restocked, written off]
    for line in body.lines:
        wanted.setdefault(line.barcode.strip().upper(), [0, 0])[0 if line.restock else 1] += line.quantity

    # The manager's PIN, with the sign-in lockout and log. Its own transaction, so a wrong PIN is still counted.
    approver = check_credentials(request.app.state.settings, body.approver_badge, body.approver_pin,
                                 "depot_console", body.terminal, event="pin_confirmed")
    approver_label = f"{approver['name']} · {approver['badge_id']}"
    requester_label = authz.actor_label(session)

    def work(conn) -> ReturnOut:
        existing = conn.execute("SELECT id FROM sale_returns WHERE client_uuid = %s", (body.client_uuid,)).fetchone()
        if existing is not None:
            return _returns(conn, "WHERE id = %s", (existing["id"],))[0]
        sale = conn.execute("SELECT * FROM transactions WHERE id = %s FOR UPDATE", (body.transaction_id,)).fetchone()
        if sale is None or not authz.same_shop(conn, session, sale["dealership_code"]):
            raise _fail(404, f"No sale with id {body.transaction_id}.")
        live = conn.execute(
            "SELECT a.role FROM accounts a JOIN employees e ON e.id = a.employee_id "
            "WHERE upper(e.badge_id) = %s AND a.is_active AND e.is_active FOR SHARE OF a",
            (approver["badge_id"].upper(),)).fetchone()
        if live is None or live["role"] not in APPROVER_ROLES:
            raise _fail(403, "A manager must approve a refund.")

        sold = _sold(conn, body.transaction_id)
        returned = _returned(conn, body.transaction_id)
        for barcode, (restocked, written_off) in wanted.items():
            if barcode not in sold:
                raise _fail(409, f"{barcode} was not on that sale.")
            available = sold[barcode][2] - returned.get(barcode, 0)
            if restocked + written_off > available:
                raise _fail(409, f"Only {available} of {sold[barcode][0]} can still come back; "
                                 f"{restocked + written_off} were asked for.")

        total = _money(sum(sold[b][1] * (r + w) for b, (r, w) in wanted.items()))
        kind, code = ("unassigned", "") if sale["dealership_code"] is None else ("dealership", sale["dealership_code"])
        shop_removed = kind == "dealership" and conn.execute(
            "SELECT 1 FROM dealerships WHERE code = %s", (code,)).fetchone() is None
        if shop_removed:  # the shop was deleted since the sale: the refund still pays out, goods go to unassigned
            kind, code = "unassigned", ""
        self_approved = approver["badge_id"].upper() == session.badge_id.upper()
        head = conn.execute(
            "INSERT INTO sale_returns (transaction_id, dealership_code, total, payment_method, reason, requested_by, "
            "approved_by, client_uuid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (body.transaction_id, sale["dealership_code"], total, sale["payment_method"], reason, requester_label,
             approver_label, body.client_uuid)).fetchone()
        for barcode, (restocked, written_off) in wanted.items():
            name, price, _ = sold[barcode]
            for quantity, restock in ((restocked, True), (written_off, False)):
                if quantity:
                    conn.execute(
                        "INSERT INTO sale_return_items (return_id, product_barcode, product_name_at_sale, "
                        "unit_price_at_sale, quantity, restock) VALUES (%s, %s, %s, %s, %s, %s)",
                        (head["id"], barcode, name, price, quantity, restock))
            if restocked:
                _change(conn, kind, code, barcode, restocked, change_total=True)
        number = f"RF-{head['id']:05d}"
        conn.execute("INSERT INTO admin_audit (actor, action, kind, target, detail) VALUES (%s, 'refunded', 'sale', %s, %s)",
                     (approver_label, f"#{body.transaction_id}",
                      f"{number} · {total:.2f} · {reason}" + (" · shop removed, restocked as unassigned" if shop_removed else "")
                      + (" · self-approved" if self_approved else "")))
        activity.record(conn, "refund", severity="warning" if self_approved else "notice", source="pos",
                        place=(kind, code), actor=approver_label, number=number, total=float(total), reason=reason,
                        sale=body.transaction_id)
        return _returns(conn, "WHERE id = %s", (head["id"],))[0]

    return _write(request, session, SELLERS, work)


# --- end of day ---------------------------------------------------------------------------

class DayCloseIn(BaseModel):
    dealership_code: str | None = Field(default=None, max_length=32)
    business_date: date
    start: datetime
    end: datetime
    counted_cash: float = Field(ge=0, le=100_000_000, allow_inf_nan=False)
    note: str | None = Field(default=None, max_length=1000)


class DaySummaryOut(BaseModel):
    dealership_code: str | None
    sales_count: int
    cash_sales: float
    card_sales: float
    other_sales: float
    cash_refunds: float
    card_refunds: float
    refunds_count: int


class DayCloseOut(BaseModel):
    id: int
    created_at: datetime
    dealership_code: str | None
    business_date: date
    sales_count: int
    cash_sales: float
    card_sales: float
    other_sales: float
    cash_refunds: float
    card_refunds: float
    expected_cash: float
    counted_cash: float
    difference: float
    note: str | None
    closed_by: str | None


def _shop(code: str | None) -> str | None:
    return (code or "").strip().upper() or None


def _summary(conn, shop: str | None, start: datetime, end: datetime) -> dict:
    sales = conn.execute(
        "SELECT payment_method, COUNT(*) AS n, COALESCE(SUM(total), 0) AS amount FROM transactions "
        "WHERE created_at >= %s AND created_at < %s AND dealership_code IS NOT DISTINCT FROM %s::text "
        "GROUP BY payment_method", (start, end, shop)).fetchall()
    refunds = conn.execute(
        "SELECT payment_method, COUNT(*) AS n, COALESCE(SUM(total), 0) AS amount FROM sale_returns "
        "WHERE created_at >= %s AND created_at < %s AND dealership_code IS NOT DISTINCT FROM %s::text "
        "GROUP BY payment_method", (start, end, shop)).fetchall()
    by = lambda rows, method: float(sum(r["amount"] for r in rows if r["payment_method"] == method))  # noqa: E731
    return {
        "dealership_code": shop, "sales_count": int(sum(r["n"] for r in sales)),
        "cash_sales": by(sales, "cash"), "card_sales": by(sales, "card"),
        "other_sales": float(sum(r["amount"] for r in sales if r["payment_method"] not in ("cash", "card"))),
        "cash_refunds": by(refunds, "cash"), "card_refunds": by(refunds, "card"),
        "refunds_count": int(sum(r["n"] for r in refunds)),
    }


def _close_out(row) -> DayCloseOut:
    return DayCloseOut(**{k: (float(row[k]) if k in _MONEY_COLUMNS else row[k]) for k in DayCloseOut.model_fields})


_MONEY_COLUMNS = {"cash_sales", "card_sales", "other_sales", "cash_refunds", "card_refunds", "expected_cash",
                  "counted_cash", "difference"}


@router.get("/day-close/summary", response_model=DaySummaryOut)
def day_summary(start: datetime, end: datetime, request: Request, dealership_code: str | None = None,
                session: Session = Depends(current_session)) -> dict:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        return _summary(conn, _shop(authz.scope_shop(conn, session, dealership_code)), start, end)


@router.post("/day-close", response_model=DayCloseOut, status_code=201)
def close_day(body: DayCloseIn, request: Request, session: Session = Depends(current_session)) -> DayCloseOut:
    authz.require(session, roles=SELLERS, areas=POS_AREA)
    note = (body.note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE:
        raise _fail(422, f"A note can have at most {MAX_NOTE} characters.")
    if body.end <= body.start:
        raise _fail(422, "The day must end after it starts.")
    counted = float(_money(body.counted_cash))
    shop = _shop(body.dealership_code)

    def work(conn) -> DayCloseOut:
        if authz.scope_shop(conn, session, shop) != shop:
            raise _fail(403, "This till belongs to another dealership.")
        s = _summary(conn, shop, body.start, body.end)
        expected = round(s["cash_sales"] - s["cash_refunds"], 2)
        difference = round(counted - expected, 2)
        row = conn.execute(
            "INSERT INTO day_closes (dealership_code, business_date, sales_count, cash_sales, card_sales, other_sales, "
            "cash_refunds, card_refunds, expected_cash, counted_cash, difference, note, closed_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (shop, body.business_date, s["sales_count"], s["cash_sales"], s["card_sales"], s["other_sales"],
             s["cash_refunds"], s["card_refunds"], expected, counted, difference, note,
             authz.actor_label(session))).fetchone()
        activity.record(conn, "day_close", severity="warning" if difference else "info", source="pos",
                        place=("dealership", shop) if shop else None, actor=authz.actor_label(session),
                        date=body.business_date.isoformat(), expected=expected, counted=counted,
                        difference=difference)
        return _close_out(row)

    return _write(request, session, SELLERS, work)


@router.get("/day-closes", response_model=list[DayCloseOut])
def list_closes(request: Request, limit: int = Query(default=60, ge=1, le=1000), dealership_code: str | None = None,
                only_differences: bool = False, session: Session = Depends(current_session)) -> list[DayCloseOut]:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        shop = _shop(authz.scope_shop(conn, session, dealership_code))
        rows = conn.execute(
            "SELECT * FROM day_closes WHERE (%s::text IS NULL OR dealership_code = %s) "
            "AND (NOT %s OR difference <> 0) ORDER BY id DESC LIMIT %s", (shop, shop, only_differences, limit)).fetchall()
        return [_close_out(r) for r in rows]


@router.get("/day-closes/latest", response_model=DayCloseOut | None)
def latest_close(business_date: date, request: Request, dealership_code: str | None = None,
                 session: Session = Depends(current_session)) -> DayCloseOut | None:
    _readers(session)
    with db.connect(request.app.state.settings) as conn:
        conn.row_factory = dict_row
        shop = _shop(authz.scope_shop(conn, session, dealership_code))
        row = conn.execute(
            "SELECT * FROM day_closes WHERE business_date = %s AND dealership_code IS NOT DISTINCT FROM %s::text "
            "ORDER BY id DESC LIMIT 1", (business_date, shop)).fetchone()
        return _close_out(row) if row else None
