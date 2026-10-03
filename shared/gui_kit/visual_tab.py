"""Generic "tab that embeds one visual + its controls" base class.

Every tab in both apps (POS's cart view, Admin's product/report tabs) is
a visual plus the controls that act on it. Subclassing VisualTab means
new sections plug into existing scaffolding - a consistent content-flow
layout where action buttons live inside the tab's own layout, never as
an absolute-positioned overlay on top of the visual (that overlap-bug
class is exactly what this base class exists to prevent).
"""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget


class VisualTab(QWidget):
    """Base class for a tab embedding one primary visual and its controls.

    Subclasses call `self.set_visual(widget)` and `self.set_controls(widget)`
    in their __init__ - both get stacked in normal document flow via a
    QVBoxLayout, so action buttons are always laid out relative to the
    content they act on rather than floated on top of it.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._visual: QWidget | None = None
        self._controls: QWidget | None = None

    def set_visual(self, widget: QWidget) -> None:
        if self._visual is not None:
            self._layout.removeWidget(self._visual)
            self._visual.deleteLater()
        self._visual = widget
        self._layout.insertWidget(0, widget)

    def set_controls(self, widget: QWidget) -> None:
        if self._controls is not None:
            self._layout.removeWidget(self._controls)
            self._controls.deleteLater()
        self._controls = widget
        self._layout.addWidget(widget)
