"""Workforce page - the real v1 build of the Employee/Workforce/
Attendance domain, replacing its themed placeholder. Mirrors
dealerships_page.py's shape: a KPI row, then a table with a row-click
detail panel and Add/Edit/Delete.

Real vs. deliberately left out, explicitly (see employee_repository.py's
and attendance_repository.py's module docstrings, and
architecture.md's "New data domains" note):
- KPI row (Total employees / On shift now / by-role breakdown): REAL,
  computed from database.employee_repository.list_all() and
  database.attendance_repository.list_open().
- Table + detail panel: REAL, showing today's actual roster
  (attendance_repository.list_roster()) - badge, name, role, location,
  today's status, and hours worked once checked out.
- Add/Edit/Delete: REAL, via EmployeeFormPopup/employee_repository, same
  pattern as DealershipsPage/DealershipFormPopup.
- The mockup's weekly shift-schedule calendar ("Shift Schedule" dialog),
  its per-week attendance-rate/absence percentages, and its per-day
  present/absent/off grid across a claimed 229 employees - ALL fabricated
  client-side (a 15-person hardcoded roster and a sine-based
  pseudo-random function), not derived from anything the mockup actually
  persists. Shown nowhere here, same call DealershipsPage's own detail
  panel already made for the mockup's fabricated revenue numbers - this
  page would rather show today's real status than a fabricated week.
  Real badge check-in/out itself lives on depot_app Console's own
  Workforce Attendance tab (attendance_panel.py) - this page reads that
  same data, it doesn't duplicate the check-in/out controls.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.employee_form_popup import EmployeeFormPopup
from admin_app.gui.components.employee_table import WorkforceTable, status_color
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING_CSS
from database import attendance_repository, employee_repository
from database.exceptions import DataAccessError
from shared.models import EMPLOYEE_ROLES, Employee


class WorkforcePage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.workforce.title"), parent, subtitle=tr("page.workforce.subtitle"))

        add_button = CompactButton("Add Employee", variant="primary")
        add_button.clicked.connect(self._open_add_popup)
        self.add_header_action(add_button)

        self._popup = EmployeeFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        self._all_employees: list[Employee] = []
        self._roster: list[dict] = []

        self.body_layout().addWidget(self._build_kpi_row())

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(16)

        table_section = Section("People & records", "Staff Roster & Attendance · today")
        self._table = WorkforceTable()
        self._table.setMinimumHeight(380)
        self._table.doubleClicked.connect(lambda _index: self._open_edit_popup())
        table_section.body_layout().addWidget(self._table)
        content_layout.addWidget(table_section, stretch=2)

        self._detail_panel = self._build_detail_panel()
        content_layout.addWidget(self._detail_panel, stretch=1)

        self.body_layout().addWidget(content, stretch=1)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self.reload()
        self._show_detail(None)

    # --- KPI row -----------------------------------------------------

    def _build_kpi_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._total_card = StatCard("Total Employees", "—")
        self._on_shift_card = StatCard("On Shift Now", "—")

        self._role_card = StatCard("By Role", "—")
        self._role_breakdown_items: dict[str, QWidget] = {}
        for role in EMPLOYEE_ROLES:
            item = stat_breakdown_item(role, "0")
            self._role_breakdown_items[role] = item
            self._role_card.footer_layout().addWidget(item)

        for card in (self._total_card, self._on_shift_card, self._role_card):
            layout.addWidget(card, stretch=1)
        return row

    # --- Detail panel --------------------------------------------------

    def _build_detail_panel(self) -> QFrame:
        p = CLASSICAL_PALETTE
        panel = QFrame()
        panel.setStyleSheet(
            f"""
            QFrame {{
                background-color: {p['background']};
                border: 1px solid {p['border']};
                border-radius: {p['radius_md']};
            }}
            QLabel {{ border: none; }}
            """
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._detail_name = QLabel()
        self._detail_name.setWordWrap(True)
        self._detail_name.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 20px; color: {p['text_primary']};")
        layout.addWidget(self._detail_name)

        self._detail_badge = QLabel()
        self._detail_badge.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self._detail_badge)

        self._detail_rows_container = QVBoxLayout()
        self._detail_rows_container.setSpacing(6)
        layout.addLayout(self._detail_rows_container)

        note = QLabel(
            "Weekly shift schedules and week-over-week attendance rates aren't tracked yet - "
            "the mockup fabricates those figures rather than persisting them. Status/hours "
            "above are today's real badge check-in/out data, logged from depot_app Console."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(note)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        self._detail_edit_button = CompactButton("Edit")
        self._detail_edit_button.clicked.connect(self._open_edit_popup)
        self._detail_delete_button = CompactButton("Delete")
        self._detail_delete_button.clicked.connect(self._delete_selected)
        button_row.addWidget(self._detail_edit_button)
        button_row.addWidget(self._detail_delete_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        return panel

    def _detail_row(self, label: str, value: str, color: str | None = None) -> QWidget:
        p = CLASSICAL_PALETTE
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label_widget = QLabel(label)
        label_widget.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        value_widget = QLabel(value)
        value_widget.setStyleSheet(f"font-size: 12px; color: {color or p['text_primary']};")
        layout.addWidget(label_widget)
        layout.addStretch(1)
        layout.addWidget(value_widget)
        return row

    def _show_detail(self, entry: dict | None) -> None:
        while self._detail_rows_container.count():
            item = self._detail_rows_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        has_selection = entry is not None
        self._detail_edit_button.setEnabled(has_selection)
        self._detail_delete_button.setEnabled(has_selection)

        if entry is None:
            self._detail_name.setText("No employee selected")
            self._detail_badge.setText("Click a row to see its detail.")
            return

        self._detail_name.setText(entry["name"])
        self._detail_badge.setText(entry["badge_id"] + (f" · {entry['title']}" if entry["title"] else ""))

        self._detail_rows_container.addWidget(self._detail_row("Role", entry["role"]))
        self._detail_rows_container.addWidget(
            self._detail_row("Location", f"{entry['location_name']} ({entry['location_type']})")
        )
        self._detail_rows_container.addWidget(
            self._detail_row("Status today", entry["status"], color=status_color(entry["status"]))
        )
        if entry["check_in_at"]:
            self._detail_rows_container.addWidget(
                self._detail_row("Checked in", entry["check_in_at"].split("T")[-1][:8])
            )
        if entry["check_out_at"]:
            self._detail_rows_container.addWidget(
                self._detail_row("Checked out", entry["check_out_at"].split("T")[-1][:8])
            )
        if entry["hours"] is not None:
            self._detail_rows_container.addWidget(self._detail_row("Hours", f"{entry['hours']:.1f}h"))
        if not entry["is_active"]:
            self._detail_rows_container.addWidget(
                self._detail_row("Account", "Inactive", color=CLASSICAL_PALETTE["text_secondary"])
            )

    def _on_selection_changed(self) -> None:
        self._show_detail(self._table.selected_row())

    # --- Data + actions --------------------------------------------------

    def reload(self) -> None:
        try:
            self._all_employees = employee_repository.list_all()
            self._roster = attendance_repository.list_roster()
            on_shift = len(attendance_repository.list_open())
        except DataAccessError:
            self._all_employees = []
            self._roster = []
            on_shift = 0
        self._table.set_roster(self._roster)

        self._total_card.set_value(str(len(self._all_employees)))
        self._on_shift_card.set_value(str(on_shift))

        for role in EMPLOYEE_ROLES:
            count = sum(1 for e in self._all_employees if e.role == role)
            item = self._role_breakdown_items[role]
            item.layout().itemAt(1).widget().setText(str(count))

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(employee=None)

    def _open_edit_popup(self) -> None:
        entry = self._table.selected_row()
        if entry is None:
            QMessageBox.information(self, "No employee selected", "Select an employee in the table first.")
            return
        try:
            employee = employee_repository.get_by_badge_id(entry["badge_id"])
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't load", str(exc))
            return
        self._popup.open_or_refresh(employee=employee)

    def _save_popup(self) -> None:
        employee = self._popup.result_employee()
        try:
            if self._popup.is_editing():
                employee_repository.update(employee)
            else:
                employee_repository.create(employee)
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self.reload()

    def _delete_selected(self) -> None:
        entry = self._table.selected_row()
        if entry is None:
            QMessageBox.information(self, "No employee selected", "Select an employee in the table first.")
            return

        confirm = QMessageBox.question(
            self, "Delete employee?", f"Delete {entry['name']}? This cannot be undone."
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            employee_repository.delete(entry["badge_id"])
        except DataAccessError as exc:
            QMessageBox.warning(self, "Couldn't delete", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Couldn't delete", str(exc))
            return
        self.reload()
        self._show_detail(None)
