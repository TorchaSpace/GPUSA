"""Base class for one page hosted in the sidebar's QStackedWidget: a
header row (page title + optional right-aligned action widgets, matching
every mockup page's `<h1>...</h1>` + action-buttons row) above a
scrollable body. Subclasses build their content into `self.body_layout()`
rather than owning their own top-level layout, so every page gets the
same header treatment for free.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS


class AdminPage(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None, subtitle: str = ""):
        super().__init__(parent)
        p = CLASSICAL_PALETTE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(20)

        header = QWidget()
        self._header_layout = QHBoxLayout(header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 30px; font-weight: 400; color: {p['text_primary']};"
        )
        titles.addWidget(self._title_label)
        # One quiet line saying what the page is for - the first thing a
        # new user needs and the last thing an experienced one notices.
        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        self._subtitle_label.setVisible(bool(subtitle))
        titles.addWidget(self._subtitle_label)
        self._header_layout.addLayout(titles)
        self._header_layout.addStretch(1)
        self._header_layout.setSpacing(8)
        self._leading_actions = 0
        outer.addWidget(header)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background-color: {p['border']}; border: none;")
        outer.addWidget(rule)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_label.setText(subtitle)
        self._subtitle_label.setVisible(bool(subtitle))

    def add_header_action(self, widget: QWidget) -> None:
        """Add a widget (e.g. a button) to the header row, right of the title."""
        self._header_layout.addWidget(widget)

    def add_leading_header_action(self, widget: QWidget) -> None:
        """Add a widget to the header row just right of the title's stretch,
        i.e. LEFT of the page's own actions - used for the controls every
        page shares (search, pending approvals)."""
        self._header_layout.insertWidget(2 + self._leading_actions, widget)
        self._leading_actions += 1

    def body_layout(self) -> QVBoxLayout:
        """Subclasses add their content widgets to this layout."""
        return self._body_layout
