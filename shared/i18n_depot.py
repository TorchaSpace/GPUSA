"""Depot app strings, merged into shared/i18n.py and shared/i18n_tr.py.

Each part lives in its own module shared/i18n_depot_<part>.py exposing `EN`
and `TR` dicts (keys "depot.<area>.<name>"). A part that does not exist yet
is simply skipped, so parts can be added one at a time."""

from __future__ import annotations

import importlib

PARTS = ("floor", "console", "portal", "pages")

EN_DEPOT: dict[str, str] = {}
TR_DEPOT: dict[str, str] = {}

for _part in PARTS:
    try:
        _module = importlib.import_module(f"shared.i18n_depot_{_part}")
    except ModuleNotFoundError:
        continue
    EN_DEPOT.update(_module.EN)
    TR_DEPOT.update(_module.TR)
