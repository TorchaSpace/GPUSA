"""Depot app strings, merged into shared/i18n.py and shared/i18n_tr.py.

Each part lives in its own module shared/i18n_depot_<part>.py exposing `EN`
and `TR` dicts (keys "depot.<area>.<name>").

The parts are imported STATICALLY on purpose: a packaged (PyInstaller) build
only bundles modules it can see in an import statement, so a dynamic
importlib lookup silently dropped every Depot string in the installed app."""

from __future__ import annotations

from shared import i18n_depot_console, i18n_depot_floor, i18n_depot_pages, i18n_depot_portal

_PARTS = (i18n_depot_floor, i18n_depot_console, i18n_depot_portal, i18n_depot_pages)

EN_DEPOT: dict[str, str] = {}
TR_DEPOT: dict[str, str] = {}

for _module in _PARTS:
    EN_DEPOT.update(_module.EN)
    TR_DEPOT.update(_module.TR)
