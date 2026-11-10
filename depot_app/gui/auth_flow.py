"""Depot sign-in (shared/auth.py, AREA_DEPOT_CONSOLE): the Floor kiosk
stays open to everyone, but its "İdari Giriş" asks for a depot manager's
(or an administrator's) badge + PIN before the Manager Console opens, and
the Console's own "İdari Giriş" asks that person's PIN again before the
Manager Portal (purchasing, treasury) unlocks. Industry-styled shared
SignInDialog."""

from __future__ import annotations

from database import account_repository, remote
from depot_app.theme import INDUSTRY_PALETTE
from shared import auth, paths
from shared.auth import Session
from shared.gui_kit.sign_in_dialog import SignInDialog
from shared.i18n import tr
from shared.models import Warehouse


def terminal_name(warehouse: Warehouse) -> str:
    return f"Depot {warehouse.code}"


def console_sign_in_dialog(warehouse: Warehouse, parent=None) -> SignInDialog:
    return SignInDialog(
        tr("depot.auth.console_title"),
        tr("depot.auth.console_sub").format(site=warehouse.site_label),
        lambda badge, pin: account_repository.authenticate(badge, pin, auth.AREA_DEPOT_CONSOLE, terminal_name(warehouse)),
        palette=INDUSTRY_PALETTE,
        parent=parent,
        server_line=True,
    )


def open_depot_dialog(parent=None) -> SignInDialog:
    """Opening the depot on a computer that works through the server: the Floor is everyone's screen, but
    the server only answers someone who signed in, so a depot manager (or an administrator) opens it once
    and the Floor keeps working with that sign-in (database/remote.keep_standing)."""
    terminal = "Depot"

    def open_up(badge: str, pin: str) -> Session:
        session = account_repository.authenticate(badge, pin, auth.AREA_DEPOT_CONSOLE, terminal)
        url = paths.get_server_url()
        if url:
            remote.keep_standing(url, session.badge_id, pin, auth.AREA_DEPOT_CONSOLE, terminal)
        return session

    return SignInDialog(
        tr("depot.auth.open_title"),
        tr("depot.auth.open_sub"),
        open_up,
        palette=INDUSTRY_PALETTE,
        cancel_text=tr("depot.auth.open_cancel"),
        parent=parent,
        server_line=True,
    )


def switch_depot_dialog(home: Warehouse, target: Warehouse, parent=None) -> SignInDialog:
    """Moving the Floor to another depot: administrators only (a depot manager stays at their own depot)."""
    return SignInDialog(
        tr("depot.auth.switch_title"),
        tr("depot.auth.switch_sub").format(site=target.site_label),
        lambda badge, pin: account_repository.approve(badge, pin, auth.AREA_ADMIN, terminal_name(home)),
        palette=INDUSTRY_PALETTE,
        parent=parent,
    )


def portal_unlock_dialog(session: Session, parent=None) -> SignInDialog:
    def confirm(_badge: str, pin: str) -> Session:
        account_repository.confirm_pin(session, pin)
        return session

    return SignInDialog(
        tr("depot.auth.portal_title"),
        tr("depot.auth.portal_sub").format(name=session.first_name),
        confirm,
        palette=INDUSTRY_PALETTE,
        fixed_badge=session.badge_id,
        parent=parent,
    )
