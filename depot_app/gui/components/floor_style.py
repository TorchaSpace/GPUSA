"""Shared pieces of the Floor's look (from the "Warehouse Floor App" mockup):
big labelled fields for a touch kiosk, the amber notice strip, and the
mockup's table with uppercase headers and generous rows."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QVBoxLayout, QWidget

from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE
from shared.textcase import upper

AMBER = "#f4b400"
AMBER_SOFT = "#ffd24d"
AMBER_PALE = "#fff6d6"


def field_label(text: str) -> QLabel:
    label = QLabel(upper(text))
    label.setStyleSheet(
        f"font-size: 11px; letter-spacing: 1.2px; font-weight: 600; color: {INDUSTRY_PALETTE['text_secondary']};"
    )
    return label


def input_style(font_px: int, heading: bool = False) -> str:
    """A big square entry box: hairline border that turns steel-blue and thickens on focus."""
    p = INDUSTRY_PALETTE
    family = f"font-family: {FONT_HEADING_CSS};" if heading else ""
    return (
        f"QLineEdit, QSpinBox {{ background-color: {p['surface_raised']}; color: {p['text_primary']}; "
        f"border: 1px solid {p['border']}; border-radius: 0; padding: 4px 10px; font-size: {font_px}px; "
        f"font-weight: 500; {family} selection-background-color: {p['accent']}; selection-color: #ffffff; }}"
        f"QLineEdit:hover, QSpinBox:hover {{ border-color: {p['text_secondary']}; }}"
        f"QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {p['accent']}; padding: 3px 9px; }}"
        f"QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}"
    )


def labelled(label: str, widget: QWidget) -> QWidget:
    host = QWidget()
    host.setStyleSheet("background: transparent;")
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(4)
    box.addWidget(field_label(label))
    box.addWidget(widget)
    return host


def notice_style(strong: bool = False) -> str:
    """The mockup's amber message bar with a heavy ink bar on its left."""
    p = INDUSTRY_PALETTE
    return (
        f"color: {p['text_primary']}; background-color: {AMBER_SOFT if strong else AMBER_PALE}; "
        f"border: none; border-left: 6px solid {p['text_primary']}; padding: 8px 12px; font-size: 14px; font-weight: 500;"
    )


def info_style() -> str:
    p = INDUSTRY_PALETTE
    return (
        f"color: {p['accent_900']}; background-color: {p['accent_100']}; border: none; "
        f"border-left: 6px solid {p['accent']}; padding: 8px 12px; font-size: 14px; font-weight: 500;"
    )


def floor_table(headers: list[str], stretch: tuple[int, ...] = (), row_height: int = 46) -> QTableWidget:
    p = INDUSTRY_PALETTE
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels([upper(h) for h in headers])
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setFocusPolicy(Qt.NoFocus)
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for column in stretch:
        header.setSectionResizeMode(column, QHeaderView.Stretch)
    table.setStyleSheet(
        f"""
        QTableWidget {{ background-color: transparent; color: {p['text_primary']}; border: none;
            font-size: 14px; outline: none; }}
        QTableWidget::item {{ padding: 0 10px; border-bottom: 1px solid {p['border']}; }}
        QHeaderView::section {{ background-color: transparent; color: {p['text_secondary']}; border: none;
            border-bottom: 2px solid {p['text_primary']}; padding: 6px 10px; font-size: 11px;
            letter-spacing: 1.2px; font-weight: 600; }}
        """
    )
    return table


def heading_label(text: str, px: int = 26) -> QLabel:
    label = QLabel(upper(text))
    label.setStyleSheet(
        f"font-family: {FONT_HEADING_CSS}; font-weight: 600; letter-spacing: 1px; font-size: {px}px; "
        f"color: {INDUSTRY_PALETTE['text_primary']};"
    )
    return label
