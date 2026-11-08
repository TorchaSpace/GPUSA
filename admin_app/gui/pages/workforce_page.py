"""Workforce page - Admin WATCHES staff and attendance here; it does not
manage them.

People are added (with their national ID) and assigned at the depot
(Console > Staff) and at the dealership till (Staff), each for its own
place only. This page shows the same data for every place:

- KPI row: total staff, in right now, staff per team (real counts from
  employee_repository / attendance_repository).
- shared/gui_kit/staff_view.StaffView in read-only mode: Today (who is in,
  when they came and left), In / out history (any dates, any person, how
  long they stayed) and Staff (badge, masked national ID, job, team, where
  they work), with a place filter.

Sign-in accounts (who may open which app) stay in Settings > Accounts.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.theme import CLASSICAL_PALETTE
from database import attendance_repository, dealership_repository, employee_repository, warehouse_repository
from database.exceptions import DATABASE_ERRORS
from shared.gui_kit.staff_view import StaffView
from shared.i18n import enum_label, tr
from shared.models import EMPLOYEE_ROLES
from shared.staff import DEALERSHIP, WAREHOUSE, StaffPlace


def all_places() -> list[StaffPlace]:
    """Every warehouse and dealership, for the place filter."""
    try:
        warehouses = warehouse_repository.list_all()
        shops = dealership_repository.list_all()
    except DATABASE_ERRORS:
        return []
    return ([StaffPlace(WAREHOUSE, w.code, w.name) for w in warehouses]
            + [StaffPlace(DEALERSHIP, d.code, d.name) for d in shops])


class WorkforcePage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("staff.page.title"), parent, subtitle=tr("staff.page.subtitle_all"))
        self.body_layout().addWidget(self._build_kpi_row())
        self.view = StaffView(CLASSICAL_PALETTE, place=None, can_manage=False, places=all_places())
        self.body_layout().addWidget(self.view, stretch=1)
        self.reload()

    def _build_kpi_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        self._total_card = StatCard(tr("admin.workforce.kpi_total"), "—")
        self._on_shift_card = StatCard(tr("admin.workforce.kpi_on_shift"), "—")
        self._role_card = StatCard(tr("admin.workforce.kpi_role"), "")
        self._role_breakdown_items: dict[str, QWidget] = {}
        for role in EMPLOYEE_ROLES:
            item = stat_breakdown_item(enum_label("role", role), "0")
            self._role_breakdown_items[role] = item
            self._role_card.footer_layout().addWidget(item)
        for card in (self._total_card, self._on_shift_card, self._role_card):
            layout.addWidget(card, stretch=1)
        return row

    def reload(self) -> None:
        """Re-read everything (MainWindow calls this each time the page is shown and on live updates)."""
        try:
            employees = [e for e in employee_repository.list_all() if e.is_active]
            on_shift = len(attendance_repository.list_open())
        except DATABASE_ERRORS:
            employees, on_shift = [], 0
        self._total_card.set_value(str(len(employees)))
        self._on_shift_card.set_value(str(on_shift))
        for role in EMPLOYEE_ROLES:
            count = sum(1 for e in employees if e.role == role)
            self._role_breakdown_items[role].layout().itemAt(1).widget().setText(str(count))
        self.view.reload()
