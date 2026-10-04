"""The store's currency: which symbol goes in front of money on screen.

The choice is saved in the store settings (ui.currency) and applied at start-up
with set_currency(). Until someone picks one, screens behave as they always did:
most show plain amounts, the till and a few Admin lines show "$" (`legacy`)."""

from __future__ import annotations

from shared.formatting import format_amount

# (code, symbol). NONE = plain amounts with no symbol at all.
CURRENCIES = (("TRY", "₺"), ("USD", "$"), ("EUR", "€"), ("GBP", "£"), ("NONE", ""))
CODES = tuple(code for code, _symbol in CURRENCIES)
_SYMBOLS = dict(CURRENCIES)

_chosen: str | None = None


def set_currency(code: str | None) -> None:
    """Use `code` (one of CODES); None goes back to the legacy behaviour."""
    global _chosen
    if code is not None and code not in _SYMBOLS:
        raise ValueError(f"Unknown currency {code!r}")
    _chosen = code


def current_currency() -> str | None:
    return _chosen


def symbol(legacy: str = "") -> str:
    """The symbol to show: the chosen one, else `legacy` (what that screen used before)."""
    return _SYMBOLS[_chosen] if _chosen is not None else legacy


def format_money(value: float | None, legacy: str = "") -> str:
    """An amount with the currency symbol in front ("₺1.234,50", "-$5.00")."""
    if value is None:
        return format_amount(None)
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol(legacy)}{format_amount(abs(value))}"
