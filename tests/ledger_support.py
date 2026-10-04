"""Shared helpers for tests that touch the ledger: every ledger mutation
needs an Actor, so most tests just want "the repository, signed in"."""

from __future__ import annotations

from shared.auth import Actor, Session

ACTOR = Actor("B-100", "Murat Yılmaz")
OTHER = Actor("B-200", "Ayşe Demir")

_MUTATORS = ("create", "update", "mark_cleared", "mark_endorsed", "reopen", "delete")


class SignedIn:
    """Wraps database.ledger_repository: mutators get `actor` filled in
    (ACTOR unless one is passed); everything else passes straight through."""

    def __init__(self, module, actor: Actor = ACTOR):
        self._module = module
        self._actor = actor

    def __getattr__(self, name):
        target = getattr(self._module, name)
        if name not in _MUTATORS:
            return target

        def call(*args, **kwargs):
            if len(args) < 2 and "actor" not in kwargs:
                kwargs["actor"] = self._actor
            return target(*args, **kwargs)

        return call


def session_for(actor: Actor = ACTOR, role: str = "admin", area: str = "admin") -> Session:
    return Session(account_id=1, badge_id=actor.badge_id, name=actor.name, role=role,
                   signed_in_at="2026-09-24T09:00:00.000Z", area=area, terminal="Test")
