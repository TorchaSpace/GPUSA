"""Bordered "section" card used inside a page body - the mockup's
recurring pattern of a bordered box with a small kicker+heading header
row (optionally holding filter controls) above its content. Distinct
from AdminPage (the whole-page title+body split) and StatCard (a single
metric) - this is for a titled sub-block like Overview/Inventory's
"Product inventory" table block.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from admin_app.theme import CLASSICAL_PALETTE


class Section(QFrame):
    def __init__(self, kicker: str, heading: str, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.setStyleSheet(
            f"""
            Section {{
                background-color: {p['background']};
                border: 1px solid {p['border']};
                border-radius: {p['radius_md']};
            }}
            QLabel {{ border: none; }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QWidget()
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        self._header.setObjectName("sectionHeader")
        self._header.setAttribute(Qt.WA_StyledBackground, True)
        self._header.setStyleSheet(f"#sectionHeader {{ border-bottom: 1px solid {p['border']}; }}")
        self._header_layout = QHBoxLayout(self._header)
        self._header_layout.setContentsMargins(16, 12, 16, 12)
        self._header_layout.setSpacing(10)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        self._kicker_label = QLabel(kicker.upper())
        self._kicker_label.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        self._kicker_label.setVisible(bool(kicker))
        titles.addWidget(self._kicker_label)
        heading_label = QLabel(heading)
        heading_label.setStyleSheet(f"font-size: 18px; color: {p['text_primary']};")
        titles.addWidget(heading_label)
        titles_widget = QWidget()
        titles_widget.setLayout(titles)
        self._header_layout.addWidget(titles_widget)
        self._header_layout.addStretch(1)

        outer.addWidget(self._header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        outer.addWidget(self._body)

    def set_kicker(self, kicker: str) -> None:
        """Change the small uppercase line above the heading (e.g. a date
        range that moves with today)."""
        self._kicker_label.setText(kicker.upper())
        self._kicker_label.setVisible(bool(kicker))

    def add_header_control(self, widget: QWidget) -> None:
        self._header_layout.addWidget(widget)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout
