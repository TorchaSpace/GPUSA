"""Pure function: Transaction in, a printable receipt document out.

No printer calls, no filesystem access, no Qt imports - this is what
lets the exact same builder feed an on-screen receipt preview AND the
actual thermal print job (pos_app/export/receipt_printer.py) without
ever being duplicated or reimplemented per output path.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.constants import RECEIPT_WIDTH_CHARS, STORE_ADDRESS_LINES, STORE_NAME
from shared.models import Transaction


@dataclass
class ReceiptDocument:
    """Plain, renderer-agnostic representation of a receipt.

    `lines` is a list of already-width-wrapped text lines at
    RECEIPT_WIDTH_CHARS, so both a plain-text print path and a PDF path
    can consume it without re-deriving layout logic.
    """

    lines: list[str]


def _centered(text: str) -> str:
    return text.center(RECEIPT_WIDTH_CHARS).rstrip()


def _two_column(left: str, right: str) -> str:
    """Pack `left`...`right` onto one RECEIPT_WIDTH_CHARS-wide line, right
    edge aligned - used for the qty/price line and the final total."""
    pad = max(1, RECEIPT_WIDTH_CHARS - len(left) - len(right))
    return f"{left}{' ' * pad}{right}"


def _wrap(text: str, width: int) -> list[str]:
    """Minimal word-wrap - a product name is short prose, not a
    paragraph, so this doesn't need textwrap's full feature set."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _line_item_lines(item) -> list[str]:
    lines = _wrap(item.product_name_at_sale, RECEIPT_WIDTH_CHARS)
    qty_price = f"{item.quantity} x {item.unit_price_at_sale:.2f}"
    lines.append(_two_column(qty_price, f"{item.line_total:.2f}"))
    return lines


def build_receipt(transaction: Transaction, store_name: str | None = None,
                  address_lines: list[str] | tuple[str, ...] | None = None) -> ReceiptDocument:
    """Build a ReceiptDocument from a finalized Transaction: store
    letterhead, timestamp/receipt number, one block per line item (name,
    then "qty x unit price" against the line total), then the total -
    all wrapped to RECEIPT_WIDTH_CHARS since a receipt's physical shape
    (narrow thermal paper) is dictated by its medium, not stretched to
    fit anything.
    """
    divider = "-" * RECEIPT_WIDTH_CHARS
    name = store_name or STORE_NAME
    address = STORE_ADDRESS_LINES if address_lines is None else address_lines
    lines: list[str] = [_centered(part) for part in _wrap(name, RECEIPT_WIDTH_CHARS)]
    lines.extend(_centered(address_line) for address_line in address)
    lines.append(divider)

    if transaction.id is not None:
        lines.append(f"Receipt #{transaction.id}")
    if transaction.created_at is not None:
        lines.append(transaction.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append(divider)

    for item in transaction.items:
        lines.extend(_line_item_lines(item))
    lines.append(divider)

    lines.append(_two_column("TOTAL", f"{transaction.total:.2f}"))
    if transaction.payment_method:
        lines.append(_two_column("PAID", transaction.payment_method.capitalize()))
    return ReceiptDocument(lines=lines)
