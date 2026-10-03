"""Exceptions raised by the data access layer.

GUI and service code should catch these rather than sqlite3 exceptions -
that keeps knowledge of "we're using SQLite" from leaking upward, which
matters when this layer is eventually swapped for a hosted backend.
"""


class DataAccessError(Exception):
    """Base class for all database/ layer errors."""


class ProductNotFoundError(DataAccessError):
    """Raised when a lookup by barcode matches no product."""

    def __init__(self, barcode: str):
        super().__init__(f"No product found with barcode {barcode!r}")
        self.barcode = barcode


class DuplicateBarcodeError(DataAccessError):
    """Raised when creating a product whose barcode already exists."""

    def __init__(self, barcode: str):
        super().__init__(f"Product with barcode {barcode!r} already exists")
        self.barcode = barcode


class TransactionNotFoundError(DataAccessError):
    """Raised when a lookup by transaction id matches no transaction."""

    def __init__(self, transaction_id: int):
        super().__init__(f"No transaction found with id {transaction_id!r}")
        self.transaction_id = transaction_id


class DealershipNotFoundError(DataAccessError):
    """Raised when a lookup by dealership code matches no dealership."""

    def __init__(self, code: str):
        super().__init__(f"No dealership found with code {code!r}")
        self.code = code


class DuplicateDealershipCodeError(DataAccessError):
    """Raised when creating a dealership whose code already exists."""

    def __init__(self, code: str):
        super().__init__(f"Dealership with code {code!r} already exists")
        self.code = code


class EmployeeNotFoundError(DataAccessError):
    """Raised when a lookup by badge id matches no employee."""

    def __init__(self, badge_id: str):
        super().__init__(f"No employee found with badge id {badge_id!r}")
        self.badge_id = badge_id


class DuplicateBadgeIdError(DataAccessError):
    """Raised when creating an employee whose badge id already exists."""

    def __init__(self, badge_id: str):
        super().__init__(f"Employee with badge id {badge_id!r} already exists")
        self.badge_id = badge_id


class AlreadyCheckedInError(DataAccessError):
    """Raised by attendance_repository.check_in() when the employee
    already has an open (not checked-out) attendance record."""

    def __init__(self, badge_id: str):
        super().__init__(f"{badge_id!r} is already checked in")
        self.badge_id = badge_id


class NoOpenAttendanceRecordError(DataAccessError):
    """Raised by attendance_repository.check_out() when the employee has
    no open attendance record to close."""

    def __init__(self, badge_id: str):
        super().__init__(f"{badge_id!r} is not currently checked in")
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
        self.barcode = barcode
        self.requested = requested
        self.available = available
        self.location = location  # display label of the place that ran short, if per-location


class PurchaseOrderNotFoundError(DataAccessError):
    """Raised when a lookup by purchase order id matches no order."""

    def __init__(self, order_id: int):
        super().__init__(f"No purchase order found with id {order_id!r}")
        self.order_id = order_id


class PurchaseOrderAlreadyDecidedError(DataAccessError):
    """Raised by purchase_order_repository.approve()/reject() when the
    order is no longer pending - e.g. another admin decided it first, or
    it never needed approval. `status` is its current status."""

    def __init__(self, order_number: str, status: str):
        super().__init__(f"{order_number} is no longer awaiting approval (it is {status!r})")
        self.order_number = order_number
        self.status = status


class LedgerEntryNotFoundError(DataAccessError):
    """Raised when a lookup by ledger entry id matches no entry."""

    def __init__(self, entry_id: int):
        super().__init__(f"No ledger entry found with id {entry_id!r}")
        self.entry_id = entry_id


class DuplicateLedgerDocumentError(DataAccessError):
    """Raised when a document with the same direction, type and number
    is already recorded (e.g. the same received check entered twice)."""

    def __init__(self, doc_no: str):
        super().__init__(f"A document numbered {doc_no!r} of that type is already recorded")
        self.doc_no = doc_no


class ShipmentNotFoundError(DataAccessError):
    """Raised when a lookup by shipment id matches no shipment."""

    def __init__(self, shipment_id: int):
        super().__init__(f"No shipment found with id {shipment_id!r}")
        self.shipment_id = shipment_id


class ShipmentStateError(DataAccessError):
    """Raised when an action doesn't fit the shipment's current status -
    e.g. dispatching one that's already delivered, or receiving one twice
    (a second terminal got there first). `status` is the current status."""

    def __init__(self, shipment_number: str, status: str, action: str):
        super().__init__(f"{shipment_number} can't be {action} - it is {status.replace('_', ' ')}")
        self.shipment_number = shipment_number
        self.status = status


class WarehouseNotFoundError(DataAccessError):
    """Raised when a warehouse code isn't in `warehouses`."""

    def __init__(self, code: str):
        super().__init__(f"No warehouse with code {code!r}")
        self.code = code


class DuplicateWarehouseCodeError(DataAccessError):
    """Raised when creating a warehouse whose code already exists."""

    def __init__(self, code: str):
        super().__init__(f"A warehouse with code {code!r} already exists")
        self.code = code


class UnknownLocationError(DataAccessError):
    """Raised when a stock operation names a warehouse or dealership code
    that doesn't exist (a typo'd code would otherwise quietly create a
    stock level nobody can see)."""

    def __init__(self, kind: str, code: str):
        super().__init__(f"No {kind} with code {code!r}")
        self.kind = kind
        self.code = code


class LocationHasStockError(DataAccessError):
    """Raised when deleting a warehouse or dealership that still holds
    stock - deleting it would lose track of those units. Move them first."""

    def __init__(self, code: str, units: int):
        super().__init__(f"{code} still holds {units} units - move them elsewhere before deleting it.")
        self.code = code
        self.units = units


# --- sign-in (database/account_repository.py) ------------------------------

class AuthError(DataAccessError):
    """Base class for sign-in / account errors; str() is safe to show."""


class SignInFailedError(AuthError):
    """Wrong badge or PIN. Deliberately doesn't say which."""

    def __init__(self, attempts_left: int | None = None):
        text = "Badge or PIN is wrong."
        if attempts_left is not None and attempts_left <= 2:
            text += f" {attempts_left} more tr{'y' if attempts_left == 1 else 'ies'} before the account locks."
        super().__init__(text)
        self.attempts_left = attempts_left


class AccountLockedError(AuthError):
    def __init__(self, until_text: str):
        super().__init__(f"Too many wrong PINs - this account is locked until {until_text}. "
                         "An administrator can unlock it in Admin > Settings.")
        self.until_text = until_text


class AccountDisabledError(AuthError):
    def __init__(self):
        super().__init__("This account is switched off. Ask an administrator.")


class NotAllowedError(AuthError):
    def __init__(self, role_text: str, area_text: str):
        super().__init__(f"A {role_text.lower()} account can't open {area_text}.")


class AccountNotFoundError(AuthError):
    def __init__(self, badge_id: str):
        super().__init__(f"No sign-in account for badge {badge_id!r}")
        self.badge_id = badge_id


class LastAdminError(AuthError):
    """Refused: it would leave no active administrator, and nobody could
    open Admin again."""

    def __init__(self):
        super().__init__("That would leave no active administrator - add or keep another one first.")


# What a page should catch around a database read: this layer's own errors
# AND raw SQLite ones ("database is locked", "unable to open database
# file", a full disk), which repositories do not wrap.
import sqlite3 as _sqlite3

DATABASE_ERRORS = (DataAccessError, _sqlite3.Error)
