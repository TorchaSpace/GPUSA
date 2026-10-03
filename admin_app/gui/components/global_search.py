"""The header search dialog (Ctrl/Cmd+K): type, pick a result, land on its page.

Ranking and matching live in shared/search.py (pure, tested); this is only
the box and the list. Records are loaded when the dialog opens, so results
are never staler than the moment you pressed the shortcut.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from database import dealership_repository, employee_repository, product_repository, warehouse_repository
from database.exceptions import DATABASE_ERRORS
from admin_app.theme import CLASSICAL_PALETTE, FONT_BODY_CSS
from shared import search as search_logic
from shared.i18n import enum_label, tr



def load_records() -> tuple[list, list, list, list]:
    """products, dealerships, warehouses, employees - an unreadable table
    just contributes nothing instead of breaking the search."""
    out = []
    for loader in (product_repository.list_all, dealership_repository.list_all,
                   warehouse_repository.list_all, employee_repository.list_all):
        try:
            out.append(loader())
        except DATABASE_ERRORS:
            out.append([])
    return tuple(out)  # type: ignore[return-value]


class GlobalSearchDialog(QDialog):
    page_chosen = Signal(str)  # nav key of the page to open

    def __init__(self, parent=None, records=None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self.setWindowTitle(tr("search.title"))
        self.setModal(True)
        self.resize(560, 420)
        self._records = records if records is not None else load_records()
        self._hits: list[search_logic.SearchHit] = []
        self.chosen: search_logic.SearchHit | None = None

        self.setStyleSheet(
            f"QDialog {{ background: {p['surface']}; }}"
            f"QLineEdit {{ background: {p['background']}; color: {p['text_primary']}; border: 1px solid {p['border']};"
            f" border-radius: 6px; padding: 8px 10px; font-family: {FONT_BODY_CSS}; font-size: 14px; }}"
            f"QListWidget {{ background: transparent; color: {p['text_primary']}; border: none; font-family: {FONT_BODY_CSS};"
            f" font-size: 13px; }}"
            f"QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}"
            f"QListWidget::item:selected {{ background: {p['surface_raised']}; color: {p['accent']}; }}"
        )
        layout = QVBoxLayout(self)
        self._input = QLineEdit()
        self._input.setPlaceholderText(tr("search.hint"))
        self._input.textChanged.connect(self.set_query)
        self._input.returnPressed.connect(self._accept_current)
        layout.addWidget(self._input)
        self._list = QListWidget()
        self._list.itemActivated.connect(lambda _item: self._accept_current())
        layout.addWidget(self._list, stretch=1)
        self._note = QLabel("")
        self._note.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        layout.addWidget(self._note)
        self.set_query("")

    def set_query(self, text: str) -> None:
        products, dealerships, warehouses, employees = self._records
        self._hits = search_logic.search(text, products, dealerships, warehouses, employees)
        self._list.clear()
        for hit in self._hits:
            item = QListWidgetItem(f"{enum_label('search_kind', hit.kind)}   {hit.label}")
            self._list.addItem(item)
        if self._hits:
            self._list.setCurrentRow(0)
            self._note.setText("")
        else:
            self._note.setText(tr("search.hint") if not text.strip() else tr("search.no_matches"))

    def hits(self) -> list[search_logic.SearchHit]:
        return list(self._hits)

    def choose(self, row: int) -> None:
        if 0 <= row < len(self._hits):
            self.chosen = self._hits[row]
            self.page_chosen.emit(self.chosen.page)
            self.accept()

    def _accept_current(self) -> None:
        self.choose(self._list.currentRow())

    def keyPressEvent(self, event) -> None:  # arrows move the list while the box keeps focus
        if event.key() in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if event.key() == Qt.Key_Down else -1
            row = max(0, min(self._list.count() - 1, self._list.currentRow() + step))
            self._list.setCurrentRow(row)
            return
        super().keyPressEvent(event)
