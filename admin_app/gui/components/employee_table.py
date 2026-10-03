"""Themed employee/attendance table: Badge / Name / Role / Location /
Status / Hours - mirrors dealership_table.py's model/view shape, but the
rows are today's roster (database.attendance_repository.list_roster()
dicts), not bare Employee objects, since the whole point of this table is
showing REAL attendance status next to each employee (see
workforce_page.py's module docstring for what that leaves out from the
mockup).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from admin_app.theme import CLASSICAL_PALETTE

_COLUMNS = ("Badge", "Name", "Role", "Location", "Status", "Hours")


def status_color(status: str) -> str:
    """Shared by the table and the detail panel."""
    p = CLASSICAL_PALETTE
    if status == "Present":
        return p["alert_success"]
    if status == "Checked out":
        return p["text_primary"]
    return p["text_secondary"]  # "Off"


class WorkforceTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._roster: list[dict] = []

    def set_roster(self, roster: list[dict]) -> None:
        self.beginResetModel()
        self._roster = roster
        self.endResetModel()

    def row_at(self, row: int) -> dict:
        return self._roster[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._roster)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return _COLUMNS[section]

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        entry = self._roster[index.row()]
        column = index.column()

        if role == Qt.DisplayRole:
            if column == 0:
                return entry["badge_id"]
            if column == 1:
                return entry["name"]
            if column == 2:
                return entry["role"]
            if column == 3:
                return f"{entry['location_name']} ({entry['location_type']})"
            if column == 4:
                return entry["status"] if entry["is_active"] else f"{entry['status']} · Inactive"
            if column == 5:
                return f"{entry['hours']:.1f}h" if entry["hours"] is not None else "—"
        elif role == Qt.ForegroundRole and column == 4:
            return QColor(status_color(entry["status"]))
        return None


class WorkforceTable(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._table_model = WorkforceTableModel(self)
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
                font-family: '{p['font_family']}';
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

    def set_roster(self, roster: list[dict]) -> None:
        self._table_model.set_roster(roster)

    def selected_row(self) -> dict | None:
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._table_model.row_at(indexes[0].row())
