"""Exceptions raised by the data access layer.

GUI and service code should catch these rather than sqlite3 exceptions -
that keeps knowledge of "we're using SQLite" from leaking upward, which
matters when this layer is eventually swapped for a hosted backend.
"""


from shared.formatting import format_int
from shared.i18n import enum_label, english, tr


def _word(group: str, code: str) -> str:
    """A status/action word in the current language (the English code with
    underscores read as spaces when there is no translation)."""
    label = enum_label(group, code)
    return label if label != code else code.replace("_", " ")


def _r(value) -> str:
    """repr(), as the English messages always quoted codes and barcodes."""
    return repr(value)


class DataAccessError(Exception):
    """Base class for all database/ layer errors.

    Messages are shown to people, so a subclass can call _localize(key, ...)
    after super().__init__(english_text): str() then returns tr(key) in the
    language the app runs in (formatted with the same values), and the
    English text when the key is missing."""

    _i18n: tuple[str, dict] | None = None

    def _localize(self, key: str, **values) -> None:
        self._i18n = (key, values)

    def __str__(self) -> str:
        if self._i18n is not None:
            key, values = self._i18n
            text = tr(key)
            if text != key:
                try:
                    return text.format(**values)
                except (KeyError, IndexError, ValueError):
                    pass
        return super().__str__()


class ProductNotFoundError(DataAccessError):
    """Raised when a lookup by barcode matches no product."""

    def __init__(self, barcode: str):
        super().__init__(f"No product found with barcode {barcode!r}")
        self._localize("err.product_not_found", barcode=_r(barcode))
        self.barcode = barcode


class DuplicateBarcodeError(DataAccessError):
    """Raised when creating a product whose barcode already exists."""

    def __init__(self, barcode: str):
        super().__init__(f"Product with barcode {barcode!r} already exists")
        self._localize("err.duplicate_barcode", barcode=_r(barcode))
        self.barcode = barcode


class TransactionNotFoundError(DataAccessError):
    """Raised when a lookup by transaction id matches no transaction."""

    def __init__(self, transaction_id: int):
        super().__init__(f"No transaction found with id {transaction_id!r}")
        self._localize("err.transaction_not_found", id=_r(transaction_id))
        self.transaction_id = transaction_id


class DealershipNotFoundError(DataAccessError):
    """Raised when a lookup by dealership code matches no dealership."""

    def __init__(self, code: str):
        super().__init__(f"No dealership found with code {code!r}")
        self._localize("err.dealership_not_found", code=_r(code))
        self.code = code


class DuplicateDealershipCodeError(DataAccessError):
    """Raised when creating a dealership whose code already exists."""

    def __init__(self, code: str):
        super().__init__(f"Dealership with code {code!r} already exists")
        self._localize("err.duplicate_dealership", code=_r(code))
        self.code = code


class EmployeeNotFoundError(DataAccessError):
    """Raised when a lookup by badge id matches no employee."""

    def __init__(self, badge_id: str):
        super().__init__(f"No employee found with badge id {badge_id!r}")
        self._localize("err.employee_not_found", badge_id=_r(badge_id))
        self.badge_id = badge_id


class DuplicateBadgeIdError(DataAccessError):
    """Raised when creating an employee whose badge id already exists."""

    def __init__(self, badge_id: str):
        super().__init__(f"Employee with badge id {badge_id!r} already exists")
        self._localize("err.duplicate_badge", badge_id=_r(badge_id))
        self.badge_id = badge_id


class AlreadyCheckedInError(DataAccessError):
    """Raised by attendance_repository.check_in() when the employee
    already has an open (not checked-out) attendance record."""

    def __init__(self, badge_id: str):
        super().__init__(f"{badge_id!r} is already checked in")
        self._localize("err.already_checked_in", badge_id=_r(badge_id))
        self.badge_id = badge_id


class NoOpenAttendanceRecordError(DataAccessError):
    """Raised by attendance_repository.check_out() when the employee has
    no open attendance record to close."""

    def __init__(self, badge_id: str):
        super().__init__(f"{badge_id!r} is not currently checked in")
        self._localize("err.not_checked_in", badge_id=_r(badge_id))
        self.badge_id = badge_id


class InsufficientStockError(DataAccessError):
    """Raised when an operation would take a product's stock below zero.

    Shared by transaction_repository.finalize_transaction() (a POS sale)
    and inventory_repository.dispatch_stock() (a depot dispatch) - both
    are "remove N units" operations that must fail the same way if N
    exceeds what's on hand.
    """

    def __init__(self, barcode: str, requested: int, available: int, location: str | None = None):
        where = f" at {location}" if location else ""
        super().__init__(
            f"Cannot remove {requested} of {barcode!r}: only {available} in stock{where}"
        )
        self._localize(
            "err.insufficient_stock_at" if location else "err.insufficient_stock",
            requested=requested, barcode=_r(barcode), available=available, location=location,
        )
        self.barcode = barcode
        self.requested = requested
        self.available = available
        self.location = location  # display label of the place that ran short, if per-location


class PurchaseOrderNotFoundError(DataAccessError):
    """Raised when a lookup by purchase order id matches no order."""

    def __init__(self, order_id: int):
        super().__init__(f"No purchase order found with id {order_id!r}")
        self._localize("err.po_not_found", id=_r(order_id))
        self.order_id = order_id


class PurchaseOrderAlreadyDecidedError(DataAccessError):
    """Raised by purchase_order_repository.approve()/reject() when the
    order is no longer pending - e.g. another admin decided it first, or
    it never needed approval. `status` is its current status."""

    def __init__(self, order_number: str, status: str):
        super().__init__(f"{order_number} is no longer awaiting approval (it is {status!r})")
        self._localize(
            "err.po_decided", number=order_number, status=_r(enum_label("status_plain", status))
        )
        self.order_number = order_number
        self.status = status


class PurchaseOrderStateError(DataAccessError):
    """Raised by purchase_order_repository.receive_against_order() /
    cancel_order() when the order's CURRENT status doesn't allow that -
    e.g. receiving against a pending, rejected, cancelled or fully
    received order, or cancelling one that is already received. `status`
    is its current status, `action` what was attempted ('received' /
    'cancelled')."""

    def __init__(self, order_number: str, status: str, action: str):
        super().__init__(f"{order_number} can't be {action} - it is {status.replace('_', ' ')!r}")
        self._localize(
            "err.po_state", number=order_number, action=_word("po_action", action),
            status=_r(enum_label("status_plain", status)),
        )
        self.order_number = order_number
        self.status = status
        self.action = action


class PurchaseOrderOverReceiveError(DataAccessError):
    """Refused: receiving more units than an order still has due."""

    def __init__(self, order_number: str, requested: int, remaining: int):
        super().__init__(
            f"{order_number}: {requested:,} units can't be received - only {remaining:,} units still due on this order."
        )
        self._localize(
            "err.po_over_receive", number=order_number, requested=format_int(requested),
            remaining=format_int(remaining),
        )
        self.order_number = order_number
        self.requested = requested
        self.remaining = remaining


class PurchaseOrderCancelNotAllowedError(DataAccessError):
    """Refused: the signed-in person may not cancel this order. `reason` is
    'role' (no depot-manager or administrator account), 'admin_only' (a
    depot manager can only cancel an order still awaiting approval) or
    'not_yours' (a depot manager's own pending orders only)."""

    _KEYS = {
        "role": ("err.po_cancel_role", "Your account can't cancel purchase orders."),
        "admin_only": ("err.po_cancel_admin_only", "Only an administrator can cancel {number} - it is already {status}."),
        "not_yours": ("err.po_cancel_not_yours", "{number} was raised by someone else - only they or an administrator can cancel it."),
    }

    def __init__(self, order_number: str, status: str, reason: str):
        key, english_text = self._KEYS[reason]
        super().__init__(english_text.format(number=order_number, status=status.replace("_", " ")))
        self._localize(key, number=order_number, status=enum_label("status_plain", status))
        self.order_number = order_number
        self.status = status
        self.reason = reason


class LedgerEntryNotFoundError(DataAccessError):
    """Raised when a lookup by ledger entry id matches no entry."""

    def __init__(self, entry_id: int):
        super().__init__(f"No ledger entry found with id {entry_id!r}")
        self._localize("err.ledger_not_found", id=_r(entry_id))
        self.entry_id = entry_id


class DuplicateLedgerDocumentError(DataAccessError):
    """Raised when a document with the same direction, type and number
    is already recorded (e.g. the same received check entered twice)."""

    def __init__(self, doc_no: str):
        super().__init__(f"A document numbered {doc_no!r} of that type is already recorded")
        self._localize("err.duplicate_ledger_doc", doc_no=_r(doc_no))
        self.doc_no = doc_no


class ShipmentNotFoundError(DataAccessError):
    """Raised when a lookup by shipment id matches no shipment."""

    def __init__(self, shipment_id: int):
        super().__init__(f"No shipment found with id {shipment_id!r}")
        self._localize("err.shipment_not_found", id=_r(shipment_id))
        self.shipment_id = shipment_id


class ShipmentStateError(DataAccessError):
    """Raised when an action doesn't fit the shipment's current status -
    e.g. dispatching one that's already delivered, or receiving one twice
    (a second terminal got there first). `status` is the current status."""

    def __init__(self, shipment_number: str, status: str, action: str):
        super().__init__(f"{shipment_number} can't be {action} - it is {status.replace('_', ' ')}")
        self._localize(
            "err.shipment_state", number=shipment_number, action=_word("shipment_action", action),
            status=_word("shipment_status", status),
        )
        self.shipment_number = shipment_number
        self.status = status


class WarehouseNotFoundError(DataAccessError):
    """Raised when a warehouse code isn't in `warehouses`."""

    def __init__(self, code: str):
        super().__init__(f"No warehouse with code {code!r}")
        self._localize("err.warehouse_not_found", code=_r(code))
        self.code = code


class DuplicateWarehouseCodeError(DataAccessError):
    """Raised when creating a warehouse whose code already exists."""

    def __init__(self, code: str):
        super().__init__(f"A warehouse with code {code!r} already exists")
        self._localize("err.duplicate_warehouse", code=_r(code))
        self.code = code


class UnknownLocationError(DataAccessError):
    """Raised when a stock operation names a warehouse or dealership code
    that doesn't exist (a typo'd code would otherwise quietly create a
    stock level nobody can see)."""

    def __init__(self, kind: str, code: str):
        super().__init__(f"No {kind} with code {code!r}")
        self._localize("err.unknown_location", kind=_word("kind", kind), code=_r(code))
        self.kind = kind
        self.code = code


class LocationHasStockError(DataAccessError):
    """Raised when deleting a warehouse or dealership that still holds
    stock - deleting it would lose track of those units. Move them first."""

    def __init__(self, code: str, units: int):
        super().__init__(f"{code} still holds {units} units - move them elsewhere before deleting it.")
        self._localize("err.location_has_stock", code=code, units=units)
        self.code = code
        self.units = units


# --- sign-in (database/account_repository.py) ------------------------------

class AuthError(DataAccessError):
    """Base class for sign-in / account errors; str() is safe to show."""


def localized_auth(key: str, **values) -> "AuthError":
    """An AuthError whose text comes from the string table (English as the
    plain message, the current language at str() time)."""
    error = AuthError(english(key, **values))
    error._localize(key, **values)
    return error


class SignInFailedError(AuthError):
    """Wrong badge or PIN. Deliberately doesn't say which."""

    def __init__(self, attempts_left: int | None = None):
        text = "Badge or PIN is wrong."
        if attempts_left is not None and attempts_left <= 2:
            text += f" {attempts_left} more tr{'y' if attempts_left == 1 else 'ies'} before the account locks."
        super().__init__(text)
        self._localize("err.sign_in_failed")
        self.attempts_left = attempts_left

    def __str__(self) -> str:
        text = super().__str__()
        n = self.attempts_left
        if n is not None and n <= 2:
            text += " " + tr("err.sign_in_tries_one" if n == 1 else "err.sign_in_tries_other").format(attempts_left=n)
        return text


class AccountLockedError(AuthError):
    def __init__(self, until_text: str):
        super().__init__(f"Too many wrong PINs - this account is locked until {until_text}. "
                         "An administrator can unlock it in Admin > Settings.")
        self._localize("err.account_locked", until=until_text)
        self.until_text = until_text


class AccountDisabledError(AuthError):
    def __init__(self):
        super().__init__("This account is switched off. Ask an administrator.")
        self._localize("err.account_disabled")


class NotAllowedError(AuthError):
    def __init__(self, role_text: str, area_text: str):
        super().__init__(f"A {role_text.lower()} account can't open {area_text}.")
        self._localize("err.not_allowed", role=role_text.lower(), area=area_text)


class AccountNotFoundError(AuthError):
    def __init__(self, badge_id: str):
        super().__init__(f"No sign-in account for badge {badge_id!r}")
        self._localize("err.account_not_found", badge_id=_r(badge_id))
        self.badge_id = badge_id


class LastAdminError(AuthError):
    """Refused: it would leave no active administrator, and nobody could
    open Admin again."""

    def __init__(self):
        super().__init__("That would leave no active administrator - add or keep another one first.")
        self._localize("err.last_admin")


class EmployeeInactiveError(DataAccessError):
    """Raised by attendance_repository.check_in() for a deactivated employee."""

    def __init__(self, badge_id: str):
        super().__init__(f"{badge_id} is marked inactive in Workforce and can't check in.")
        self._localize("err.employee_inactive", badge_id=badge_id)
        self.badge_id = badge_id


class SessionInvalidError(AuthError):
    """The signed-in account was switched off, its employee deactivated,
    removed, or its role changed since sign-in."""

    def __init__(self):
        super().__init__("This sign-in is no longer valid - the account was changed or switched off. Sign in again.")
        self._localize("err.session_invalid")


class SelfActionError(AuthError):
    """An administrator tried to demote, switch off or remove their own account."""

    def __init__(self, action: str):
        super().__init__(f"You can't {action} your own account - ask another administrator.")
        key = {"change the role of": "err.self_role", "switch off": "err.self_off", "remove": "err.self_remove"}.get(action)
        if key:
            self._localize(key)
        self.action = action


class DealershipInactiveError(AuthError):
    """A till (POS) belongs to a dealership that is switched off in Admin:
    it can't take sales."""

    def __init__(self, name: str = ""):
        who = f"{name} is" if name else "This dealership is"
        super().__init__(f"{who} switched off in Admin, so its till can't be used. Ask an administrator.")
        self._localize("err.dealership_inactive_named" if name else "err.dealership_inactive", name=name)
        self.name = name


# What a page should catch around a database read: this layer's own errors
# AND raw SQLite ones ("database is locked", "unable to open database
# file", a full disk), which repositories do not wrap.
import sqlite3 as _sqlite3

DATABASE_ERRORS = (DataAccessError, _sqlite3.Error)


class LedgerEntryStateError(DataAccessError):
    """Raised by ledger_repository when a transition or edit isn't allowed
    from the entry's CURRENT status - e.g. clearing an already-cleared
    entry (another admin got there first), or editing/deleting a settled
    one. `status` is its current status, `action` what was attempted."""

    def __init__(self, entry_id: int, status: str, action: str):
        super().__init__(f"Can't {action} ledger entry {entry_id}: it is {status!r}")
        known = enum_label("ledger_status", status)
        self._localize(
            "err.ledger_state", id=entry_id, action=_word("ledger_action", action),
            status=known if known != status else _r(status),
        )
        self.entry_id = entry_id
        self.status = status
        self.action = action


# --- products / locations / shipments: refusals added with the stock-safety fixes ----

class ProductInUseError(DataAccessError):
    """Refused: deleting a product that still has stock or any history
    (stock movements, sales, shipment lines). Deactivate it instead."""

    def __init__(self, barcode: str, reason: str, reason_key: str | None = None, **reason_values):
        super().__init__(f"{barcode} can't be deleted: {reason}. Deactivate it instead.")
        if reason_key:
            reason = tr(reason_key).format(**reason_values)
        self._localize("err.product_in_use", barcode=barcode, reason=reason)
        self.barcode = barcode
        self.reason = reason


class LocationInUseError(DataAccessError):
    """Refused: deleting a warehouse/dealership that scheduled or in-transit
    shipments still point at (as origin or destination)."""

    def __init__(self, kind: str, code: str, shipments: int):
        super().__init__(
            f"{kind.capitalize()} {code} can't be deleted: {shipments} shipment{'s' if shipments != 1 else ''} "
            "still scheduled or in transit. Deactivate it instead, or finish/cancel those shipments first."
        )
        self._localize(
            "err.location_in_use_one" if shipments == 1 else "err.location_in_use_other",
            kind=_word("kind", kind).capitalize(), code=code, n=format_int(shipments),
        )
        self.kind = kind
        self.code = code
        self.shipments = shipments


class ShipmentNotDispatchedError(ShipmentStateError):
    """Refused: receiving a shipment that never left (status scheduled) -
    it would create stock out of nothing. Dispatch it first."""

    def __init__(self, shipment_number: str, status: str):
        super().__init__(shipment_number, status, "received")
        self.args = (f"{shipment_number} can't be received - it hasn't been dispatched yet "
                     f"(it is {status.replace('_', ' ')}). The depot must dispatch it first.",)
        self._localize("err.not_dispatched", number=shipment_number, status=_word("shipment_status", status))


class LocationInactiveError(DataAccessError):
    """Refused: putting stock into a deactivated warehouse."""

    def __init__(self, code: str):
        super().__init__(f"Warehouse {code} is inactive - reactivate it before putting stock there.")
        self._localize("err.location_inactive", code=code)
        self.code = code


class CapacityExceededError(DataAccessError):
    """Refused: an inbound movement would take a warehouse over its capacity."""

    def __init__(self, code: str, capacity: int, used: int, adding: int):
        super().__init__(
            f"Warehouse {code} holds {used:,} of {capacity:,} units - adding {adding:,} would exceed its capacity "
            f"by {used + adding - capacity:,}."
        )
        self._localize(
            "err.capacity_exceeded", code=code, used=format_int(used), capacity=format_int(capacity),
            adding=format_int(adding), over=format_int(used + adding - capacity),
        )
        self.code = code
        self.capacity = capacity
        self.used = used
        self.adding = adding


class CapacityBelowUsageError(DataAccessError):
    """Refused: setting a warehouse's capacity below the units it holds now."""

    def __init__(self, code: str, capacity: int, used: int):
        super().__init__(
            f"Warehouse {code} holds {used:,} units, which is more than the new capacity of {capacity:,}. "
            "Move stock out first, or set a capacity of at least that many units."
        )
        self._localize("err.capacity_below_usage", code=code, used=format_int(used), capacity=format_int(capacity))
        self.code = code
        self.capacity = capacity
        self.used = used


class ProductInactiveError(ProductNotFoundError):
    """Raised when an inactive (deactivated) product is looked up for a sale
    or a movement. It is still in the database, just no longer in use."""

    def __init__(self, barcode: str):
        DataAccessError.__init__(self, f"Product {barcode!r} is deactivated and can't be used")
        self._localize("err.product_inactive", barcode=_r(barcode))
        self.barcode = barcode


class PriceChangedError(DataAccessError):
    """Raised by transaction_repository.finalize_transaction() when a cart
    line's price is no longer the product's current price (an admin changed
    it between adding the line and charging). Nothing was written.

    `changes` lists every line that moved as (barcode, old_price, new_price)
    tuples - old is what the cart held, new what the database holds now."""

    def __init__(self, changes):
        self.changes = [(str(b), float(old), float(new)) for b, old, new in changes]
        lines = "; ".join(f"{b!r}: {old:,.2f} -> {new:,.2f}" for b, old, new in self.changes)
        super().__init__(f"Price changed since the item was added to the cart ({lines}). Nothing was charged.")
        self._localize("err.price_changed", lines="; ".join(
            f"{b}: {old:,.2f} → {new:,.2f}" for b, old, new in self.changes))
        first = self.changes[0] if self.changes else ("", 0.0, 0.0)
        self.barcode, self.old_price, self.new_price = first


class StockRequestNotFoundError(DataAccessError):
    """Raised when a lookup by stock request id matches no request."""

    def __init__(self, request_id: int):
        super().__init__(f"No stock request found with id {request_id!r}")
        self._localize("err.stock_request_not_found", id=_r(request_id))
        self.request_id = request_id


class StockRequestStateError(DataAccessError):
    """Raised when an action doesn't fit a stock request's current status -
    e.g. declining one the depot already planned, or cancelling one twice
    (another terminal got there first). `status` is the current status."""

    def __init__(self, number: str, status: str, action: str):
        super().__init__(f"{number} can't be {action} - it is {status}")
        self._localize(
            "err.stock_request_state", number=number, action=_word("stock_request_action", action),
            status=_word("stock_request_status", status),
        )
        self.number = number
        self.status = status


class DuplicateStockRequestError(DataAccessError):
    """Raised when a dealership asks for a product it already has an open request for."""

    def __init__(self, number: str, product_name: str):
        super().__init__(f"There is already an open request for {product_name} ({number})")
        self._localize("err.stock_request_duplicate", number=number, product=product_name)
        self.number = number


class ReturnQuantityError(DataAccessError):
    """Raised when a refund asks for more of a product than the sale still
    has out (sold minus already returned). `available` may be 0."""

    def __init__(self, product: str, requested: int, available: int):
        super().__init__(f"Only {available} of {product} can still be returned (asked for {requested})")
        self._localize("err.return_qty", product=product, requested=requested, available=available)
        self.available = available


class ReturnApprovalError(AuthError):
    """The refund's approver is not a signed-off manager: their account is
    switched off, or their role may not open the depot console / Admin."""

    def __init__(self):
        super().__init__("A manager or administrator has to approve a refund.")
        self._localize("err.return_approver")

