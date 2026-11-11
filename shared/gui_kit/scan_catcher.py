"""ScanCatcher: a barcode / QR reader works wherever the cursor is.

A USB reader is a keyboard: it types the code very fast and presses Enter.
That only lands somewhere useful while the cursor sits in the right box. A
cashier who just tapped a product card, or a picker who just closed a
dialog, has the focus on a button, and the read used to vanish.

ScanCatcher watches the keys of one window. While the focus is NOT in a
place where people type (a line edit, a number box, a text area, an
editable combo box), it collects printable keys and, on Enter (or Tab,
which some readers send instead), hands the collected code to `on_scan`.
Keys typed into a text box are never touched, so typing a name, a note or
a quantity works exactly as before. Nothing happens while a modal dialog
or a popup is open, while `enabled()` says no, or for shortcuts
(Ctrl/Cmd + key). A pause of more than PAUSE_SECONDS between keys starts
a new code, so a stray key pressed earlier never sticks to the next read.
"""

from __future__ import annotations

import time
import weakref
from typing import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

PAUSE_SECONDS = 1.0
_END_KEYS = (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab)


def is_typing_place(widget: QWidget | None) -> bool:
    """True where keys belong to the widget: text, number and editable combo boxes."""
    if widget is None:
        return False
    if isinstance(widget, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit)):
        return True
    return isinstance(widget, QComboBox) and widget.isEditable()


class ScanCatcher(QObject):
    """Catches reader input for `window`; see the module docstring."""

    def __init__(self, window: QWidget, on_scan: Callable[[str], None],
                 enabled: Callable[[], bool] | None = None):
        super().__init__(window)
        self._window = weakref.ref(window)
        self._on_scan = on_scan
        self._enabled = enabled or (lambda: True)
        self._buffer = ""
        self._last_key = 0.0
        app = QApplication.instance()
        app.installEventFilter(self)
        window.destroyed.connect(lambda *_a, app=app: app.removeEventFilter(self))

    def pending(self) -> str:
        """What has been collected so far (tests)."""
        return self._buffer

    def _mine(self, obj: QObject) -> bool:
        window = self._window()
        if window is None or not isinstance(obj, QWidget) or obj.window() is not window:
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        # Only the widget the key is for: once a key bubbles up to a parent, it was already seen.
        return focus is None or focus is obj

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt's name)
        if event.type() != QEvent.KeyPress or not self._mine(obj):
            return False
        if is_typing_place(obj) or not self._enabled():
            self._buffer = ""
            return False

        now = time.monotonic()
        if now - self._last_key > PAUSE_SECONDS:
            self._buffer = ""
        self._last_key = now

        key = event.key()
        if key in _END_KEYS:
            if not self._buffer:
                return False  # a plain Enter / Tab: let the button or focus chain have it
            code, self._buffer = self._buffer, ""
            self._on_scan(code)
            return True
        if key == Qt.Key_Escape:
            self._buffer = ""
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier) and not event.modifiers() & Qt.AltModifier:
            return False  # a shortcut (Ctrl+Alt is AltGr on Windows: that one types characters)
        text = event.text()
        if not text or not text.isprintable() or (not text.strip() and not self._buffer):
            return False
        self._buffer += text
        return True
