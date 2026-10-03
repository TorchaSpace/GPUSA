"""Generic "popup that refreshes in place rather than reopening" base class.

Both apps need transient popups (e.g. POS's low-stock alert, Admin's
add/edit product dialog). Rather than each app writing its own
open/close/rebuild-from-scratch logic, they subclass RefreshablePopup and
override refresh_content() - calling .open(...) again on an already-open
instance updates it in place instead of stacking a second window.
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QWidget


class RefreshablePopup(QDialog):
    """Base class for a popup that can be told to redraw itself in place.

    Subclasses override `refresh_content(**kwargs)` to rebuild whatever
    they display. Callers should keep a single instance around and call
    `open_or_refresh(**kwargs)` rather than constructing a new popup each
    time - this is the pattern that avoids the overlay/duplicate-window
    class of bugs called out in the architecture doc.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

    def refresh_content(self, **kwargs) -> None:
        """Subclasses rebuild their body here. Must be safe to call repeatedly."""
        raise NotImplementedError

    def open_or_refresh(self, **kwargs) -> None:
        self.refresh_content(**kwargs)
        if not self.isVisible():
            self.show()
        else:
            self.raise_()
            self.activateWindow()
