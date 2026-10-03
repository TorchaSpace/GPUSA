"""The till's sign-in: a cashier (or an administrator) signs in with
badge + PIN before the POS opens, and again after "Switch cashier" at
shift change. Uses the shared SignInDialog with the Organic palette; the
rules are in shared/auth.py (AREA_POS)."""

from __future__ import annotations

from database import account_repository, dealership_repository
from database.exceptions import DATABASE_ERRORS, DealershipInactiveError, DealershipNotFoundError
from shared import auth
from shared.gui_kit.sign_in_dialog import SignInDialog
from pos_app.theme import ORGANIC_PALETTE


def terminal_name(dealership_code: str | None) -> str:
    return f"POS {dealership_code}" if dealership_code else "POS"


def _switched_off_dealership(dealership_code: str | None):
    """The Dealership row if this till's dealership exists and is switched
    off, else None (no dealership - a dev run -, not in the database yet, or
    an unreadable database never block the till)."""
    if not dealership_code:
        return None
    try:
        dealership = dealership_repository.get_by_code(dealership_code)
    except (DealershipNotFoundError, *DATABASE_ERRORS):
        return None
    return None if dealership.is_active else dealership


def till_dealership_problem(dealership_code: str | None) -> str | None:
    """Why this till must not sell (its dealership is switched off in
    Admin), or None. Checked at sign-in here; the sale path should call it
    as well, since a dealership can be switched off while a cashier is
    already signed in."""
    dealership = _switched_off_dealership(dealership_code)
    return str(DealershipInactiveError(dealership.name)) if dealership else None


def _authenticate_at_till(dealership_code: str | None, badge: str, pin: str):
    """Sign-in for the till: refused outright while its dealership is
    switched off (before any PIN is looked at)."""
    dealership = _switched_off_dealership(dealership_code)
    if dealership:
        raise DealershipInactiveError(dealership.name)
    return account_repository.authenticate(badge, pin, auth.AREA_POS, terminal_name(dealership_code))


def till_sign_in_dialog(dealership_code: str | None, dealership_name: str, parent=None,
                        cancel_text: str = "Close till") -> SignInDialog:
    subtitle = f"{dealership_name} · sign in with your badge and PIN to open the till."
    try:
        if not account_repository.list_accounts():
            subtitle += " No accounts exist yet - an administrator adds them in Admin > Settings."
    except Exception:
        pass
    closed = till_dealership_problem(dealership_code)
    if closed:
        subtitle += " " + closed
    return SignInDialog(
        "Who's on the till?",
        subtitle,
        lambda badge, pin: _authenticate_at_till(dealership_code, badge, pin),
        palette=ORGANIC_PALETTE,
        cancel_text=cancel_text,
        parent=parent,
    )
