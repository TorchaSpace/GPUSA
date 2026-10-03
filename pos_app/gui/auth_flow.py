"""The till's sign-in: a cashier (or an administrator) signs in with
badge + PIN before the POS opens, and again after "Switch cashier" at
shift change. Uses the shared SignInDialog with the Organic palette; the
rules are in shared/auth.py (AREA_POS)."""

from __future__ import annotations

from database import account_repository
from shared import auth
from shared.gui_kit.sign_in_dialog import SignInDialog
from pos_app.theme import ORGANIC_PALETTE


def terminal_name(dealership_code: str | None) -> str:
    return f"POS {dealership_code}" if dealership_code else "POS"


def till_sign_in_dialog(dealership_code: str | None, dealership_name: str, parent=None,
                        cancel_text: str = "Close till") -> SignInDialog:
    subtitle = f"{dealership_name} · sign in with your badge and PIN to open the till."
    try:
        if not account_repository.list_accounts():
            subtitle += " No accounts exist yet - an administrator adds them in Admin > Settings."
    except Exception:
        pass
    return SignInDialog(
        "Who's on the till?",
        subtitle,
        lambda badge, pin: account_repository.authenticate(badge, pin, auth.AREA_POS, terminal_name(dealership_code)),
        palette=ORGANIC_PALETTE,
        cancel_text=cancel_text,
        parent=parent,
    )
