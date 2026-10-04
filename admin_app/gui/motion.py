"""Admin's motion helpers now live in shared/gui_kit/motion.py (Depot uses them
too); this module keeps the old import path working."""

from shared.gui_kit.motion import *  # noqa: F401,F403
from shared.gui_kit.motion import (  # noqa: F401  (private names the tests reach for)
    _KEEP,
    _DialogFader,
    _alive,
    _format_number,
    _parse_number,
    _remember,
)
