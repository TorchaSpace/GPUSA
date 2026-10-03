"""Small Industry-styled building blocks for the Console's pages
(Dashboard, Inventory, Reports): a kicker label, a read-only table, a
stat cell (the mockup's square KPI boxes), and a card frame with a title."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from PySide6.QtGui import QColor

from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.theme import FONT_HEADING_CSS, INDUSTRY_PALETTE

AMBER = "#f4b400"


def kicker(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setStyleSheet(f"font-size: 12px; letter-spacing: 1px; color: {INDUSTRY_PALETTE['text_secondary']};")
    return label


def industry_table(headers: list[str], selectable: bool = False) -> QTableWidget:
    p = INDUSTRY_PALETTE
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    if selectable:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
    else:
        table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setStyleSheet(
        f"""
        QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
            border: 1px solid {p['border']}; gridline-color: {p['border']}; font-size: 13px; }}
        QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
            border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
        QTableWidget::item:selected {{ background-color: {p['accent_100']}; color: {p['text_primary']}; }}
        """
    )
    return table


def item(text, right: bool = False, color: str | None = None) -> QTableWidgetItem:
    cell = QTableWidgetItem(str(text))
    if right:
        cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if color:
        cell.setForeground(QColor(color))
    return cell


class StatCell(BlueprintFrame):
    """A KPI box: caption, big value, a small note line."""

    def __init__(self, caption: str, parent: QWidget | None = None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        self.setObjectName("statCell")
        self.setStyleSheet(f"#statCell {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        cap = QLabel(caption.upper())
        cap.setStyleSheet(f"font-size: 11px; letter-spacing: 1px; color: {p['text_secondary']};")
        layout.addWidget(cap)
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; "
                                       f"color: {p['text_primary']};")
        layout.addWidget(self.value_label)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self.note_label)

    def set(self, value: str, note: str = "", warn: bool = False) -> None:
        p = INDUSTRY_PALETTE
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 600; font-size: 28px; "
                                       f"color: {'#b07f00' if warn else p['text_primary']};")
        self.note_label.setText(note)


class Card(BlueprintFrame):
    """A titled panel; add content to .body."""

    def __init__(self, title: str, parent: QWidget | None = None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        self.setObjectName("consoleCard")
        self.setStyleSheet(f"#consoleCard {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        self.title_label = kicker(title)
        self.body.addWidget(self.title_label)
