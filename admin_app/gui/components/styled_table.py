"""Classical-themed QTableWidget + cell helper for admin pages that show
simple read-mostly rows (Purchase Requests, Treasury & Ledger) - the same
look as dealership_table.py's model/view table, for pages whose rows
are rebuilt wholesale on every reload and don't need a custom model."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

from admin_app.theme import CLASSICAL_PALETTE


def styled_table(headers: list[str]) -> QTableWidget:
    p = CLASSICAL_PALETTE
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)  # hairline rules between rows come from the items, not a full grid
    table.setFocusPolicy(Qt.StrongFocus)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)  # smooth, not row-jumping
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.verticalHeader().setDefaultSectionSize(40)  # room to breathe, like the mockup's rows
    table.horizontalHeader().setHighlightSections(False)
    table.setCornerButtonEnabled(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.setStyleSheet(
        f"""
        QTableWidget {{
            background-color: {p['background']}; alternate-background-color: {p['surface']};
            color: {p['text_primary']}; gridline-color: {p['border']}; border: none;
            font-family: {p['font_family_css']}; font-size: 13px;
            selection-background-color: rgba(225, 173, 102, 30); selection-color: {p['text_primary']};
        }}
        QHeaderView::section {{
            background-color: {p['surface']}; color: {p['text_secondary']}; border: none;
            border-bottom: 1px solid {p['border']}; padding: 10px 10px; font-size: 11px; font-weight: 600;
        }}
        QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {p['border']}; }}
        QTableWidget::item:hover {{ background-color: rgba(234, 231, 231, 12); }}
        QTableWidget::item:selected {{ background-color: rgba(225, 173, 102, 30); color: {p['text_primary']}; }}
        """
    )
    return table


def cell(text: str, right: bool = False, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        item.setForeground(QColor(color))
    return item
