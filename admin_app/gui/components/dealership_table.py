"""Themed dealership table: Code / Name / Region / City / Manager /
Status - mirrors inventory_table.py's model/view shape exactly, with a
dealership's `is_active` flag standing in for a product's stock-based
status column.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from shared.i18n import region_label, tr
from admin_app.theme import CLASSICAL_PALETTE
from shared.models import Dealership

_COLUMN_KEYS = ("code", "name", "region", "city", "manager", "status")


def _columns() -> tuple[str, ...]:
    return tuple(tr(f"admin.dealerships.col_{key}") for key in _COLUMN_KEYS)


def status_for(dealership: Dealership) -> tuple[str, str]:
    """Return (label, hex color) - shared by the table and the detail panel."""
    p = CLASSICAL_PALETTE
    if dealership.is_active:
        return tr("admin.dealerships.status_active"), p["alert_success"]
    return tr("admin.dealerships.status_inactive"), p["text_secondary"]


class DealershipTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dealerships: list[Dealership] = []

    def set_dealerships(self, dealerships: list[Dealership]) -> None:
        self.beginResetModel()
        self._dealerships = dealerships
        self.endResetModel()

    def dealership_at(self, row: int) -> Dealership:
        return self._dealerships[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._dealerships)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLUMN_KEYS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return _columns()[section]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        dealership = self._dealerships[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == 0:
                return dealership.code
            if column == 1:
                return dealership.name
            if column == 2:
                return region_label(dealership.region)
            if column == 3:
                return dealership.city
            if column == 4:
                return dealership.manager_name or "—"
            if column == 5:
                return status_for(dealership)[0]
        elif role == Qt.ForegroundRole and column == 5:
            return QColor(status_for(dealership)[1])
        return None


class DealershipTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._table_model = DealershipTableModel(self)
        self.setModel(self._table_model)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.verticalHeader().setVisible(False)

        p = CLASSICAL_PALETTE
        self.setStyleSheet(
            f"""
            QTableView {{
                background-color: {p['background']};
                alternate-background-color: {p['surface']};
                color: {p['text_primary']};
                gridline-color: {p['border']};
                border: none;
                font-family: {p['font_family_css']};
                font-size: 13px;
                selection-background-color: transparent;
            }}
            QHeaderView::section {{
                background-color: {p['surface']};
                color: {p['text_secondary']};
                border: none;
                border-bottom: 1px solid {p['border']};
                padding: 8px 10px;
                font-size: 11px;
                letter-spacing: 1px;
            }}
            QTableView::item {{
                padding: 4px 10px;
            }}
            QTableView::item:selected {{
                background-color: rgba(225, 173, 102, 30);
                color: {p['text_primary']};
            }}
            """
        )

    def set_dealerships(self, dealerships: list[Dealership]) -> None:
        self._table_model.set_dealerships(dealerships)

    def select_code(self, code: str) -> bool:
        """Select the row for `code` (case-insensitive); False if absent."""
        wanted = (code or "").strip().upper()
        for row in range(self._table_model.rowCount()):
            if self._table_model.dealership_at(row).code.upper() == wanted:
                self.selectRow(row)
                return True
        return False

    def selected_dealership(self) -> Dealership | None:
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._table_model.dealership_at(indexes[0].row())
