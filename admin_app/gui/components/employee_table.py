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
from shared.formatting import format_number
from shared.i18n import enum_label, tr

_COLUMN_KEYS = ("badge", "name", "role", "location", "status", "hours")


def _columns() -> tuple[str, ...]:
    return tuple(tr(f"admin.workforce.col_{key}") for key in _COLUMN_KEYS)


def hours_text(hours: float | None) -> str:
    """"7.5h" ("7,5 sa" in Turkish), or "—" for no hours yet."""
    if hours is None:
        return "—"
    return tr("admin.workforce.hours_fmt").format(h=format_number(hours, 1))


def location_text(entry: dict) -> str:
    """"Merkez Depo (Warehouse)" - the place's name from the database, the
    type translated."""
    return tr("admin.workforce.location_fmt").format(
        name=entry["location_name"], kind=enum_label("location_type", entry["location_type"])
    )


def status_text(entry: dict) -> str:
    """The Status cell: the status, ' · Inactive' for a switched-off
    employee, and a warning when an open shift looks forgotten (open for
    more than attendance_repository.LONG_SHIFT_HOURS)."""
    status = enum_label("attendance", entry["status"])
    text = status if entry["is_active"] else f"{status} · {tr('admin.workforce.status_inactive')}"
    if entry.get("long_open"):
        text += " · " + tr("admin.workforce.long_open")
    return text


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
        return 0 if parent.isValid() else len(_COLUMN_KEYS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return _columns()[section]

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
                return enum_label("role", entry["role"])
            if column == 3:
                return location_text(entry)
            if column == 4:
                return status_text(entry)
            if column == 5:
                return hours_text(entry["hours"])
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
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)  # a title is never cut off
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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

    def set_roster(self, roster: list[dict]) -> None:
        self._table_model.set_roster(roster)

    def select_badge(self, badge_id: str) -> bool:
        """Select the row for `badge_id` (case-insensitive); False if absent."""
        wanted = (badge_id or "").strip().upper()
        for row in range(self._table_model.rowCount()):
            if self._table_model.row_at(row)["badge_id"].upper() == wanted:
                self.selectRow(row)
                return True
        return False

    def selected_row(self) -> dict | None:
        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._table_model.row_at(indexes[0].row())
