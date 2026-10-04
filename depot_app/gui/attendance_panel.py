"""Workforce Attendance panel - the real build of the Warehouse Console
mockup's "Workforce Attendance" tab (Warehouse Console.dc.html, opsTabs'
'att' tab): a badge check-in/check-out form, an on-floor count, and
today's roster table (Badge/Name/Role/Status/Checked in/Checked out/
Hours) - recreated field-for-field, since unlike admin_app's
Workforce.dc.html (see workforce_page.py's docstring), this mockup tab
is a genuine, buildable workflow with no fabricated numbers at all.

Real, not a placeholder: wired through database.attendance_repository's
check_in()/check_out() (atomic BEGIN IMMEDIATE, same pattern as
inventory_repository's receive_stock()/dispatch_stock()) and
list_open()/list_roster().

Employees themselves are managed in admin_app's Workforce page
(employee_repository) - this panel only checks existing badges in/out,
it doesn't add new employee records (mirrors the mockup: its check-in
form takes a badge id, it has no "add employee" control of its own).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import attendance_repository, employee_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import FONT_HEADING, INDUSTRY_PALETTE
from shared.auth import normalize_badge_id
from shared.formatting import local_clock_text
from shared.i18n import enum_label, tr


class AttendancePanel(QWidget):
    """Emits `attendance_changed` after a successful check-in/out, so a
    host window can refresh anything else that shows on-floor counts."""

    attendance_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        p = INDUSTRY_PALETTE

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        card = BlueprintFrame(tick_color=p["text_primary"])
        card.setStyleSheet(f"background-color: {p['surface']}; border: 1px solid {p['border']};")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(12)

        card_layout.addWidget(self._build_form())

        self._error_label = QLabel()
        self._error_label.setStyleSheet(
            f"color: {p['text_primary']}; background-color: #fff6d6; "
            f"border: 1px solid #f4b400; padding: 6px 10px; font-size: 12px;"
        )
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        card_layout.addWidget(self._error_label)

        status_row = QHBoxLayout()
        self._on_floor_label = QLabel()
        self._on_floor_label.setStyleSheet(f"font-size: 14px; color: {p['text_primary']};")
        status_row.addWidget(self._on_floor_label)
        status_row.addStretch(1)
        card_layout.addLayout(status_row)

        self._table = self._build_table()
        card_layout.addWidget(self._table, stretch=1)

        outer.addWidget(card)
        self.reload()

    def _build_form(self) -> QWidget:
        p = INDUSTRY_PALETTE
        form = QHBoxLayout()
        form.setSpacing(8)

        self._badge_input = QLineEdit()
        self._badge_input.setPlaceholderText(tr("depot.attendance.scan_badge"))
        self._badge_input.setStyleSheet(
            f"QLineEdit {{ background-color: {p['background']}; color: {p['text_primary']}; "
            f"border: 1px solid {p['border']}; border-radius: 0; padding: 10px 10px; "
            f"font-size: 18px; }}"
        )
        form.addWidget(self._badge_input, stretch=1)

        check_in_button = IndustryButton(tr("depot.checkin.check_in"), variant="primary")
        check_in_button.clicked.connect(self._on_check_in)
        self._badge_input.returnPressed.connect(self._on_check_in)
        form.addWidget(check_in_button)

        check_out_button = IndustryButton(tr("depot.checkin.check_out"), variant="ghost")
        check_out_button.clicked.connect(self._on_check_out)
        form.addWidget(check_out_button)

        widget = QWidget()
        widget.setLayout(form)
        return widget

    def _build_table(self) -> QTableWidget:
        p = INDUSTRY_PALETTE
        table = QTableWidget(0, 7)
        table.setHorizontalHeaderLabels(
            [tr(f"depot.attendance.col_{c}") for c in ("badge", "name", "role", "status", "in", "out", "hours")]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setStyleSheet(
            f"""
            QTableWidget {{ background-color: {p['background']}; color: {p['text_primary']};
                border: 1px solid {p['border']}; gridline-color: {p['border']}; }}
            QHeaderView::section {{ background-color: {p['surface']}; color: {p['text_secondary']};
                border: none; border-bottom: 1px solid {p['border']}; padding: 4px; font-size: 11px; }}
            """
        )
        return table

    def _on_check_in(self) -> None:
        badge_id = normalize_badge_id(self._badge_input.text())
        if not badge_id:
            self._show_error(tr("depot.checkin.scan_first"))
            return
        try:
            attendance_repository.check_in(badge_id)
        except DATABASE_ERRORS as exc:
            self._show_error(str(exc))
            return
        self._after_success()

    def _on_check_out(self) -> None:
        badge_id = normalize_badge_id(self._badge_input.text())
        if not badge_id:
            self._show_error(tr("depot.checkin.scan_first"))
            return
        try:
            attendance_repository.check_out(badge_id)
        except DATABASE_ERRORS as exc:
            self._show_error(str(exc))
            return
        self._after_success()

    def _after_success(self) -> None:
        self._error_label.hide()
        self._badge_input.clear()
        self._badge_input.setFocus()
        self.reload()
        self.attendance_changed.emit()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()

    def reload(self) -> None:
        try:
            on_floor = len(attendance_repository.list_open())
            total = len(employee_repository.list_all())
            roster = attendance_repository.list_roster()
        except DATABASE_ERRORS:
            on_floor, total, roster = 0, 0, []

        self._on_floor_label.setText(tr("depot.attendance.on_floor").format(on=on_floor, total=total))

        self._table.setRowCount(len(roster))
        for row, entry in enumerate(roster):
            self._table.setItem(row, 0, QTableWidgetItem(entry["badge_id"]))
            self._table.setItem(row, 1, QTableWidgetItem(entry["name"]))
            self._table.setItem(row, 2, QTableWidgetItem(enum_label("role", entry["role"])))
            status = enum_label("attendance", entry["status"]) + (" · " + tr("admin.workforce.long_open") if entry.get("long_open") else "")
            self._table.setItem(row, 3, QTableWidgetItem(status))
            # Local time (a shift from an earlier day also shows its date), not the UTC text of the stamp.
            in_text = local_clock_text(entry["check_in_at"])
            out_text = local_clock_text(entry["check_out_at"])
            self._table.setItem(row, 4, QTableWidgetItem(in_text))
            self._table.setItem(row, 5, QTableWidgetItem(out_text))
            hours_text = f"{entry['hours']:.1f}" if entry["hours"] is not None else "—"
            self._table.setItem(row, 6, QTableWidgetItem(hours_text))
