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

from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING


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
        if corner_note:
            note = QLabel(corner_note)
            note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
            top_row.addWidget(note)
        outer.addLayout(top_row)

        value_row = QHBoxLayout()
        value_row.setSpacing(10)
        self._value_label = QLabel(value)
        self._value_label.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-size: 40px; font-weight: 400; color: {p['text_primary']};"
        )
        value_row.addWidget(self._value_label)
        if trend_text:
            trend_color = (
                p["alert_success"]
                if trend_positive is True
                else p["alert_critical"]
                if trend_positive is False
                else p["text_secondary"]
            )
            trend = QLabel(trend_text)
            trend.setStyleSheet(f"font-size: 13px; color: {trend_color};")
            value_row.addWidget(trend)
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
