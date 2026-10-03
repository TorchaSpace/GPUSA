"""Metric card: a small uppercase label ("kicker"), an optional corner
note, a large heading-font value with an optional trend/annotation, and
an optional footer area for a breakdown row or mini progress bars -
matches the 3-up metric-card grid at the top of the mockup's Overview
page (Inventory Dashboard.dc.html): "Total revenue · MTD", "Warehouses",
"Dealerships".

Callers needing the footer breakdown (e.g. Overview's "Wholesale/Dealer/
Direct" row, or per-warehouse capacity bars) add widgets to
`footer_layout()` themselves - StatCard only owns the header+value part
every card shares, same division of responsibility as AdminPage's
title+body split.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS


class StatCard(QFrame):
    def __init__(
        self,
        label: str,
        value: str,
        trend_text: str = "",
        trend_positive: bool | None = None,
        corner_note: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.setStyleSheet(
            f"""
            StatCard {{
                background-color: {p['background']};
                border: 1px solid {p['border']};
                border-radius: {p['radius_md']};
            }}
            QLabel {{ border: none; }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        top_row = QHBoxLayout()
        kicker = QLabel(label.upper())
        kicker.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        top_row.addWidget(kicker)
        top_row.addStretch(1)
        self._corner_label = QLabel(corner_note)
        self._corner_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        self._corner_label.setVisible(bool(corner_note))
        top_row.addWidget(self._corner_label)
        outer.addLayout(top_row)

        value_row = QHBoxLayout()
        value_row.setSpacing(10)
        self._value_label = QLabel(value)
        self._value_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 40px; font-weight: 400; color: {p['text_primary']};"
        )
        value_row.addWidget(self._value_label)
        self._trend_label = QLabel()
        value_row.addWidget(self._trend_label)
        self.set_trend(trend_text, trend_positive)
        value_row.addStretch(1)
        outer.addLayout(value_row)

        self._footer_frame = QWidget()
        self._footer_layout = QHBoxLayout(self._footer_frame)
        self._footer_layout.setContentsMargins(0, 10, 0, 0)
        self._footer_layout.setSpacing(0)
        # Scoped by object name: a selector-less stylesheet cascades to every
        # child label, drawing a stray line/box around each of them.
        self._footer_frame.setObjectName("statCardFooter")
        self._footer_frame.setAttribute(Qt.WA_StyledBackground, True)
        self._footer_frame.setStyleSheet(f"#statCardFooter {{ border-top: 1px solid {p['border']}; }}")
        self._footer_frame.setVisible(False)
        outer.addWidget(self._footer_frame)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_trend(self, text: str, positive: bool | None = None) -> None:
        """The small coloured annotation beside the value (green when
        `positive`, red when False, muted when None); hidden when empty."""
        p = CLASSICAL_PALETTE
        color = (
            p["alert_success"] if positive is True else p["alert_critical"] if positive is False else p["text_secondary"]
        )
        self._trend_label.setText(text)
        self._trend_label.setStyleSheet(f"font-size: 13px; color: {color};")
        self._trend_label.setVisible(bool(text))

    def set_corner_note(self, text: str) -> None:
        self._corner_label.setText(text)
        self._corner_label.setVisible(bool(text))

    def clear_footer(self) -> None:
        """Drop everything added through footer_layout() (for a card that
        redraws its breakdown on every reload)."""
        while self._footer_layout.count():
            item = self._footer_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._footer_frame.setVisible(False)

    def footer_layout(self) -> QHBoxLayout:
        """Callers add breakdown widgets here; the footer area auto-shows once used."""
        self._footer_frame.setVisible(True)
        return self._footer_layout


def stat_breakdown_item(label: str, value: str) -> QWidget:
    """Small "label above value" pair used inside a StatCard's footer row
    (e.g. Overview's Wholesale/Dealer/Direct breakdown)."""
    p = CLASSICAL_PALETTE
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    label_widget = QLabel(label)
    label_widget.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']}; border: none;")
    value_widget = QLabel(value)
    value_widget.setStyleSheet(f"font-size: 13px; color: {p['text_primary']}; border: none;")
    layout.addWidget(label_widget)
    layout.addWidget(value_widget)
    return widget
