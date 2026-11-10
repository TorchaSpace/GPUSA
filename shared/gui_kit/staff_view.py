"""Staff & attendance view - one widget, three apps, each in its own palette.

* The depot Console's "Staff" page and the till's "Staff" page manage THEIR
  OWN place's people (`place` = this warehouse / this dealership,
  `can_manage=True`): add a person with their national ID, change their job
  or team, mark them as left, check them in and out, and see who came and
  who left on any day.
* Admin's Workforce page only watches (`place=None`, `can_manage=False`):
  the same lists for every depot and dealership, with a place filter.

Three tabs, nothing else to learn:
  Today       - who is in right now, when they came, when they left
                (+ the check-in / check-out box where people are managed)
  History     - every check-in and check-out between two dates, per person,
                with the date, the weekday and how long they stayed
  Staff       - the people, their badge, (masked) national ID, job, team

Data: database.employee_repository / attendance_repository; the rules
(which place a person belongs to, national ID checks) are enforced there and
on the server, the form only checks early so mistakes show at once.
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import attendance_repository, employee_repository
from database.exceptions import DATABASE_ERRORS
from shared import current_session
from shared import national_id as national_ids
from shared.auth import normalize_badge_id, role_label
from shared.i18n import enum_label, tr
from shared.models import EMPLOYEE_ROLES, Employee
from shared.staff import (
    StaffPlace,
    clock_on,
    date_text,
    duration_text,
    period,
    total_minutes,
    weekday_text,
    worked_minutes,
)

LONG_SHIFT_HOURS = attendance_repository.LONG_SHIFT_HOURS


def _css(p: dict) -> dict[str, str]:
    """The few style snippets every part uses, built from an app palette."""
    radius = p.get("radius_sm", "6px")
    return {
        "input": (f"background: {p.get('surface_raised', p['background'])}; color: {p['text_primary']}; "
                  f"border: 1px solid {p['border']}; border-radius: {radius}; padding: 7px 10px; font-size: 14px;"),
        "primary": (f"QPushButton {{ background: {p['accent']}; color: white; border: none; border-radius: {radius}; "
                    f"padding: 9px 18px; font-size: 14px; font-weight: 600; }} "
                    f"QPushButton:hover {{ background: {p.get('accent_900', p['accent'])}; }}"),
        "ghost": (f"QPushButton {{ background: transparent; color: {p['text_primary']}; border: 1px solid {p['border']}; "
                  f"border-radius: {radius}; padding: 9px 18px; font-size: 14px; font-weight: 600; }} "
                  f"QPushButton:hover {{ background: {p['surface']}; }}"),
        "chip": (f"QPushButton {{ background: transparent; color: {p['text_secondary']}; border: 1px solid {p['border']}; "
                 f"border-radius: {radius}; padding: 6px 12px; font-size: 13px; }} "
                 f"QPushButton:checked {{ background: {p['accent']}; color: white; border-color: {p['accent']}; }}"),
        "table": (f"QTableWidget {{ background: {p['background']}; color: {p['text_primary']}; border: 1px solid {p['border']}; "
                  f"gridline-color: {p['border']}; font-size: 13px; }} "
                  f"QTableWidget::item {{ padding: 4px 6px; }} "
                  f"QTableWidget::item:selected {{ background: {p['accent']}; color: white; }} "
                  f"QHeaderView::section {{ background: {p['surface']}; color: {p['text_secondary']}; border: none; "
                  f"border-bottom: 1px solid {p['border']}; padding: 6px; font-size: 12px; font-weight: 600; }}"),
        "muted": f"color: {p['text_secondary']}; font-size: 13px;",
        "strong": f"color: {p['text_primary']}; font-size: 15px; font-weight: 600;",
    }


def _button(text: str, style: str) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    button.setStyleSheet(style)
    return button


def _table(headers: list[str], css: str, stretch: int = 0) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setAlternatingRowColors(False)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setSectionResizeMode(stretch, QHeaderView.Stretch)
    table.setStyleSheet(css)
    return table


def _item(text: str, color: str | None = None, bold: bool = False, data=None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if color:
        item.setForeground(_qcolor(color))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    if data is not None:
        item.setData(Qt.UserRole, data)
    return item


def _qcolor(hex_color: str):
    from PySide6.QtGui import QColor

    return QColor(hex_color)


def _role_text(role: str | None) -> str:
    return enum_label("role", role) if role else "—"


# Head-office people (root, Admin accounts) are kept as working at "HQ" with the job title the account
# was made with; both are shown in the screen's language.
HEAD_OFFICE = "HQ"
_ACCOUNT_TITLES = {"Administrator": "admin", "Depot manager": "depot_manager", "Cashier": "cashier"}


def _title_text(title: str | None) -> str:
    role = _ACCOUNT_TITLES.get(title or "")
    return role_label(role) if role else (title or "")


def _place_text(entry: dict) -> str:
    name = entry.get("location_name") or ""
    kind = entry.get("location_type")
    if name == HEAD_OFFICE:
        return tr("staff.place.head_office")
    return f"{enum_label('location_type', kind)} · {name}" if kind else name


# --- add / edit form -------------------------------------------------------------------

class StaffFormDialog(QDialog):
    """Add a person to `place`, or change one of its people. Everything is checked when Save is pressed
    and the dialog stays open with the reason; `save` (a callable taking the Employee and whether it is
    an edit) does the database work and raises what to show."""

    def __init__(self, palette: dict, place: StaffPlace, save, employee: Employee | None = None,
                 suggested_badge: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._palette = palette
        self._place = place
        self._save = save
        self._editing = employee
        p = palette
        css = _css(p)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowTitle(tr("staff.form.edit_title").format(name=employee.name) if employee
                            else tr("staff.form.add_title"))
        self.setObjectName("staffForm")
        self.setStyleSheet(f"#staffForm {{ background: {p['background']}; }} QLabel {{ color: {p['text_primary']}; }}"
                           f" QLineEdit, QComboBox {{ {css['input']} }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setStyleSheet(f"font-size: 20px; font-weight: 600; color: {p['text_primary']};")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.name_input = QLineEdit(employee.name if employee else "")
        self.name_input.setPlaceholderText(tr("staff.form.name_hint"))
        form.addRow(tr("staff.form.name") + " *", self.name_input)

        self.national_id_input = QLineEdit()
        self.national_id_input.setMaxLength(14)
        saved = employee.national_id if employee else None
        self._needs_national_id = not saved
        self.national_id_input.setPlaceholderText(
            tr("staff.form.national_id_saved").format(masked=national_ids.mask(saved)) if saved
            else tr("staff.form.national_id_hint"))
        form.addRow(tr("staff.form.national_id") + (" *" if self._needs_national_id else ""), self.national_id_input)

        self.badge_input = QLineEdit(employee.badge_id if employee else suggested_badge)
        self.badge_input.setPlaceholderText(tr("staff.form.badge_hint"))
        self.badge_input.setEnabled(employee is None)  # the badge is the record's key
        form.addRow(tr("staff.form.badge") + " *", self.badge_input)

        self.title_input = QLineEdit((employee.title or "") if employee else "")
        self.title_input.setPlaceholderText(tr("staff.form.title_hint"))
        form.addRow(tr("staff.form.title"), self.title_input)

        self.role_input = QComboBox()
        for role in EMPLOYEE_ROLES:
            self.role_input.addItem(enum_label("role", role), role)
        default_role = "Sales & service" if place.kind == "Dealership" else "Operations"
        self.role_input.setCurrentIndex(max(0, self.role_input.findData(employee.role if employee else default_role)))
        form.addRow(tr("staff.form.role"), self.role_input)

        place_label = QLabel(place.label)
        place_label.setStyleSheet(css["strong"])
        form.addRow(tr("staff.form.place"), place_label)

        self.active_input = QCheckBox(tr("staff.form.active"))
        self.active_input.setChecked(employee.is_active if employee else True)
        if employee is not None:
            form.addRow("", self.active_input)
        layout.addLayout(form)

        hint = QLabel(tr("staff.form.required"))
        hint.setStyleSheet(css["muted"])
        layout.addWidget(hint)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {p.get('alert_critical', '#b3261e')}; font-size: 14px;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = _button(tr("staff.form.cancel"), css["ghost"])
        cancel.clicked.connect(self.reject)
        self.save_button = _button(tr("staff.form.save"), css["primary"])
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.submit)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        (self.name_input if employee else self.name_input).setFocus()

    def employee(self) -> Employee:
        typed_id = national_ids.normalize(self.national_id_input.text()) or None
        return Employee(
            badge_id=normalize_badge_id(self.badge_input.text()),
            name=" ".join(self.name_input.text().split()),
            title=" ".join(self.title_input.text().split()) or None,
            role=self.role_input.currentData(),
            location_type=self._place.kind,
            location_name=self._editing.location_name if self._editing else (self._place.name or self._place.code),
            is_active=self.active_input.isChecked(),
            national_id=typed_id,
        )

    def problem(self) -> str | None:
        employee = self.employee()
        try:
            employee_repository.validate_fields(employee, require_national_id=self._needs_national_id)
        except ValueError as exc:
            return str(exc)
        return None

    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()

    def submit(self) -> None:
        problem = self.problem()
        if problem:
            self.show_error(problem)
            return
        try:
            self._save(self.employee(), self._editing is not None)
        except (*DATABASE_ERRORS, ValueError) as exc:
            self.show_error(str(exc))
            return
        self.accept()


# --- the view --------------------------------------------------------------------------

class StaffView(QWidget):
    """See the module docstring. Emits `changed` after a check-in/out or a saved person, so a host can
    refresh anything else that counts people on the floor."""

    changed = Signal()
    TABS = ("today", "history", "people")

    def __init__(self, palette: dict, *, place: StaffPlace | None, can_manage: bool, area: str | None = None,
                 places: list[StaffPlace] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._p = palette
        self._css = _css(palette)
        self._place = place
        self._can_manage = bool(can_manage and place is not None)
        self._area = area
        self._places = places or []
        self._roster: list[dict] = []
        self._history: list[dict] = []
        self._people: list[dict] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        self.tab_buttons: dict[str, QPushButton] = {}
        for i, key in enumerate(self.TABS):
            button = _button(tr(f"staff.tab.{key}"), self._css["chip"])
            button.setCheckable(True)
            self._tab_group.addButton(button, i)
            self.tab_buttons[key] = button
            top.addWidget(button)
        top.addStretch(1)
        self.place_filter = QComboBox()
        self.place_filter.setStyleSheet(self._css["input"])
        self.place_filter.addItem(tr("staff.history.every_place"), None)
        for option in self._places:
            self.place_filter.addItem(f"{enum_label('location_type', option.kind)} · {option.label}", option)
        self.place_filter.setVisible(place is None and bool(self._places))
        self.place_filter.currentIndexChanged.connect(lambda _i: self.reload())
        top.addWidget(self.place_filter)
        outer.addLayout(top)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_today())
        self._stack.addWidget(self._build_history())
        self._stack.addWidget(self._build_people())
        outer.addWidget(self._stack, stretch=1)
        self._tab_group.idClicked.connect(self.show_tab)
        self.show_tab(0)

    # -- scope --------------------------------------------------------------------------

    def _scope(self) -> StaffPlace | None:
        if self._place is not None:
            return self._place
        return self.place_filter.currentData()

    def _mine(self, entries: list[dict]) -> list[dict]:
        scope = self._scope()
        return [e for e in entries if scope is None or scope.has(e.get("location_type"), e.get("location_name"))]

    # -- today --------------------------------------------------------------------------

    def _build_today(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        if self._can_manage:
            box = QHBoxLayout()
            box.setSpacing(8)
            self.badge_input = QLineEdit()
            self.badge_input.setPlaceholderText(tr("staff.punch.placeholder"))
            self.badge_input.setMinimumHeight(44)
            self.badge_input.setStyleSheet(self._css["input"] + " font-size: 17px;")
            self.badge_input.returnPressed.connect(self.check_in)
            self.badge_input.textEdited.connect(lambda _t: self.punch_message.hide())
            self.in_button = _button(tr("staff.punch.in"), self._css["primary"])
            self.in_button.setMinimumHeight(44)
            self.in_button.clicked.connect(self.check_in)
            self.out_button = _button(tr("staff.punch.out"), self._css["ghost"])
            self.out_button.setMinimumHeight(44)
            self.out_button.clicked.connect(self.check_out)
            box.addWidget(self.badge_input, stretch=1)
            box.addWidget(self.in_button)
            box.addWidget(self.out_button)
            layout.addLayout(box)
        self.punch_message = QLabel()
        self.punch_message.setWordWrap(True)
        self.punch_message.hide()
        layout.addWidget(self.punch_message)
        self.today_count = QLabel()
        self.today_count.setStyleSheet(self._css["strong"])
        layout.addWidget(self.today_count)
        headers = ["name", "badge", "title", "status", "in", "out", "duration"]
        if self._place is None:
            headers.insert(3, "place")
        self._today_cols = headers
        self.today_table = _table([tr(f"staff.col.{h}") for h in headers], self._css["table"])
        layout.addWidget(self.today_table, stretch=1)
        self.today_empty = QLabel(tr("staff.today.empty") if self._can_manage else tr("staff.today.empty_readonly"))
        self.today_empty.setStyleSheet(self._css["muted"])
        self.today_empty.hide()
        layout.addWidget(self.today_empty)
        return page

    def _punch(self, action: str) -> None:
        badge = normalize_badge_id(self.badge_input.text())
        if not badge:
            self._say(tr("staff.punch.scan_first"), ok=False)
            return
        scope = {"warehouse_code": self._place.code} if self._place.kind == "Warehouse" else {
            "dealership_code": self._place.code}
        try:
            if action == "in":
                attendance_repository.check_in(badge, **scope)
            else:
                attendance_repository.check_out(badge, **scope)
        except DATABASE_ERRORS as exc:
            self._say(str(exc), ok=False)
            return
        name = next((r["name"] for r in self._roster if r["badge_id"].upper() == badge), badge)
        key = "staff.punch.done_in" if action == "in" else "staff.punch.done_out"
        self._say(tr(key).format(name=name, time=datetime.now().strftime("%H:%M")), ok=True)
        self.badge_input.clear()
        self.badge_input.setFocus()
        self.reload()
        self.changed.emit()

    def check_in(self) -> None:
        self._punch("in")

    def check_out(self) -> None:
        self._punch("out")

    def _say(self, text: str, ok: bool) -> None:
        p = self._p
        color = p.get("alert_success", p["accent"]) if ok else p.get("alert_critical", "#b3261e")
        self.punch_message.setStyleSheet(f"color: {p['text_primary']}; border-left: 4px solid {color}; "
                                         f"background: {p['surface']}; padding: 8px 12px; font-size: 14px;")
        self.punch_message.setText(text)
        self.punch_message.show()

    def _fill_today(self) -> None:
        roster = [r for r in self._mine(self._roster) if r["is_active"] or r["status"] == "Present"]
        on = sum(1 for r in roster if r["status"] == "Present")
        self.today_count.setText(tr("staff.today.count").format(on=on, total=len(roster)))
        # In first (longest in at the top), then those who left, then the rest.
        order = {"Present": 0, "Checked out": 1}
        roster.sort(key=lambda r: (order.get(r["status"], 2), r["check_in_at"] or "", r["name"]))
        p = self._p
        self.today_table.setRowCount(len(roster))
        for row, entry in enumerate(roster):
            present = entry["status"] == "Present"
            if present:
                status = tr("staff.state.in")
                color = p.get("alert_success", p["accent"])
            elif entry["status"] == "Checked out":
                status, color = tr("staff.state.out"), p["text_primary"]
            else:
                status, color = tr("staff.state.off"), p["text_secondary"]
            if entry.get("long_open"):
                status = tr("staff.state.long").format(h=LONG_SHIFT_HOURS)
                color = p.get("alert_critical", color)
            today = date.today()
            minutes = worked_minutes(entry["check_in_at"], entry["check_out_at"]) if entry["check_in_at"] else None
            values = {
                "name": _item(entry["name"], bold=True, data=entry["badge_id"]),
                "badge": _item(entry["badge_id"]),
                "title": _item(_title_text(entry.get("title")) or _role_text(entry.get("role"))),
                "place": _item(_place_text(entry)),
                "status": _item(status, color=color, bold=present),
                "in": _item(clock_on(entry["check_in_at"], today)),
                "out": _item(clock_on(entry["check_out_at"], today)),
                "duration": _item(duration_text(minutes) if minutes is not None else "—"),
            }
            for col, key in enumerate(self._today_cols):
                self.today_table.setItem(row, col, values[key])
        self.today_empty.setVisible(not roster)

    # -- history ------------------------------------------------------------------------

    def _build_history(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        self.preset_buttons: dict[str, QPushButton] = {}
        for key in ("today", "yesterday", "week", "month"):
            button = _button(tr(f"staff.history.{key}"), self._css["chip"])
            button.setCheckable(True)
            button.clicked.connect(lambda _c, k=key: self.set_period(k))
            self._preset_group.addButton(button)
            self.preset_buttons[key] = button
            bar.addWidget(button)
        bar.addSpacing(12)
        self.from_date = self._date_edit()
        self.to_date = self._date_edit()
        for label, edit in ((tr("staff.history.from"), self.from_date), (tr("staff.history.to"), self.to_date)):
            text = QLabel(label)
            text.setStyleSheet(self._css["muted"])
            bar.addWidget(text)
            bar.addWidget(edit)
        bar.addSpacing(12)
        self.person_filter = QComboBox()
        self.person_filter.setStyleSheet(self._css["input"])
        self.person_filter.setMinimumWidth(180)
        self.person_filter.addItem(tr("staff.history.everyone"), None)
        self.person_filter.currentIndexChanged.connect(lambda _i: self._load_history())
        bar.addWidget(self.person_filter)
        bar.addStretch(1)
        layout.addLayout(bar)
        headers = ["date", "day", "name", "badge", "in", "out", "duration"]
        if self._place is None:
            headers.insert(4, "place")
        self._history_cols = headers
        self.history_table = _table([tr(f"staff.col.{h}") for h in headers], self._css["table"], stretch=2)
        layout.addWidget(self.history_table, stretch=1)
        self.history_summary = QLabel()
        self.history_summary.setStyleSheet(self._css["strong"])
        layout.addWidget(self.history_summary)
        self.set_period("today", load=False)
        self.from_date.dateChanged.connect(self._dates_typed)
        self.to_date.dateChanged.connect(self._dates_typed)
        return page

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd.MM.yyyy")
        edit.setStyleSheet(self._css["input"])
        return edit

    def set_period(self, preset: str, load: bool = True) -> None:
        first, last = period(preset)
        for edit, day in ((self.from_date, first), (self.to_date, last)):
            edit.blockSignals(True)
            edit.setDate(QDate(day.year, day.month, day.day))
            edit.blockSignals(False)
        self.preset_buttons[preset].setChecked(True)
        if load:
            self._load_history()

    def _dates_typed(self, *_args) -> None:
        self._preset_group.setExclusive(False)
        for button in self.preset_buttons.values():
            button.setChecked(False)
        self._preset_group.setExclusive(True)
        self._load_history()

    def _period(self) -> tuple[date, date]:
        return self.from_date.date().toPython(), self.to_date.date().toPython()

    def _load_history(self) -> None:
        first, last = self._period()
        try:
            rows = attendance_repository.list_history(first, last, place=self._scope(),
                                                      badge_id=self.person_filter.currentData())
        except DATABASE_ERRORS:
            rows = []
        self._history = rows
        p = self._p
        self.history_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            out = clock_on(entry["check_out_at"], entry["day"]) if entry["check_out_at"] else tr("staff.history.still_in")
            values = {
                "date": _item(date_text(entry["day"])),
                "day": _item(weekday_text(entry["day"])),
                "name": _item(entry["name"], bold=True),
                "badge": _item(entry["badge_id"]),
                "place": _item(_place_text(entry)),
                "in": _item(clock_on(entry["check_in_at"], entry["day"])),
                "out": _item(out, color=p.get("alert_success", p["accent"]) if entry["open"] else None),
                "duration": _item(duration_text(entry["minutes"])),
            }
            for col, key in enumerate(self._history_cols):
                self.history_table.setItem(row, col, values[key])
        if rows:
            self.history_summary.setText(tr("staff.history.summary").format(
                n=len(rows), hours=duration_text(total_minutes(rows))))
        else:
            self.history_summary.setText(tr("staff.history.empty"))

    def _fill_person_filter(self) -> None:
        current = self.person_filter.currentData()
        self.person_filter.blockSignals(True)
        self.person_filter.clear()
        self.person_filter.addItem(tr("staff.history.everyone"), None)
        for entry in sorted(self._mine(self._roster), key=lambda e: e["name"]):
            self.person_filter.addItem(f"{entry['name']} · {entry['badge_id']}", entry["badge_id"])
        self.person_filter.setCurrentIndex(max(0, self.person_filter.findData(current)))
        self.person_filter.blockSignals(False)

    # -- people -------------------------------------------------------------------------

    def _build_people(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        bar = QHBoxLayout()
        self.people_count = QLabel()
        self.people_count.setStyleSheet(self._css["strong"])
        bar.addWidget(self.people_count)
        bar.addStretch(1)
        self.add_button = self.edit_button = None
        if self._can_manage:
            self.edit_button = _button(tr("staff.people.edit"), self._css["ghost"])
            self.edit_button.clicked.connect(self.edit_selected)
            self.add_button = _button(tr("staff.people.add"), self._css["primary"])
            self.add_button.clicked.connect(self.add_person)
            bar.addWidget(self.edit_button)
            bar.addWidget(self.add_button)
        layout.addLayout(bar)
        headers = ["name", "badge", "national_id", "title", "role", "status"]
        if self._place is None:
            headers.insert(5, "place")
        self._people_cols = headers
        self.people_table = _table([tr(f"staff.col.{h}") for h in headers], self._css["table"])
        if self._can_manage:
            self.people_table.doubleClicked.connect(lambda _i: self.edit_selected())
        layout.addWidget(self.people_table, stretch=1)
        hint = QLabel(tr("staff.people.hint") if self._can_manage else tr("staff.people.readonly_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(self._css["muted"])
        layout.addWidget(hint)
        self.people_message = QLabel()
        self.people_message.setWordWrap(True)
        self.people_message.setStyleSheet(f"color: {self._p.get('alert_critical', '#b3261e')}; font-size: 14px;")
        self.people_message.hide()
        layout.addWidget(self.people_message)
        return page

    def _fill_people(self) -> None:
        people = sorted(self._mine(self._roster), key=lambda e: (not e["is_active"], e["name"]))
        self._people = people
        p = self._p
        self.people_count.setText(tr("staff.people.count").format(n=sum(1 for e in people if e["is_active"])))
        self.people_table.setRowCount(len(people))
        for row, entry in enumerate(people):
            active = entry["is_active"]
            values = {
                "name": _item(entry["name"], bold=True, data=entry["badge_id"]),
                "badge": _item(entry["badge_id"]),
                "national_id": _item(national_ids.mask(entry.get("national_id"))),
                "title": _item(_title_text(entry.get("title")) or "—"),
                "role": _item(_role_text(entry.get("role"))),
                "place": _item(_place_text(entry)),
                "status": _item(tr("staff.people.active") if active else tr("staff.people.inactive"),
                                color=None if active else p["text_secondary"]),
            }
            for col, key in enumerate(self._people_cols):
                self.people_table.setItem(row, col, values[key])

    def selected_badge(self) -> str | None:
        row = self.people_table.currentRow()
        if row < 0 or row >= len(self._people):
            return None
        return self._people[row]["badge_id"]

    def _save(self, employee: Employee, editing: bool) -> None:
        actor = current_session.actor()
        if editing:
            employee_repository.update(employee, by=actor, place=self._place, area=self._area)
        else:
            employee_repository.create(employee, place=self._place, by=actor, area=self._area,
                                       require_national_id=True)

    def form(self, employee: Employee | None = None) -> StaffFormDialog:
        """The add / edit dialog for this place (not shown yet - tests drive it directly)."""
        suggested = ""
        if employee is None:
            try:
                suggested = employee_repository.next_badge_id(self._place.code)
            except DATABASE_ERRORS:
                suggested = ""
        return StaffFormDialog(self._p, self._place, self._save, employee, suggested, self)

    def _run_form(self, dialog: StaffFormDialog) -> None:
        if dialog.exec():
            self.reload()
            self.changed.emit()
        dialog.deleteLater()

    def add_person(self) -> None:
        self._run_form(self.form())

    def edit_selected(self) -> None:
        badge = self.selected_badge()
        if badge is None:
            self.people_message.setText(tr("staff.people.select_first"))
            self.people_message.show()
            return
        self.people_message.hide()
        try:
            employee = employee_repository.get_by_badge_id(badge)
        except DATABASE_ERRORS as exc:
            self.people_message.setText(str(exc))
            self.people_message.show()
            return
        self._run_form(self.form(employee))

    # -- navigation / data ----------------------------------------------------------------

    def show_tab(self, index: int) -> None:
        self.tab_buttons[self.TABS[index]].setChecked(True)
        self._stack.setCurrentIndex(index)
        self.reload()

    def current_tab(self) -> str:
        return self.TABS[self._stack.currentIndex()]

    def reload(self) -> None:
        """Re-read what the open tab shows (hosts call it on a live update)."""
        try:
            self._roster = attendance_repository.list_roster()
        except DATABASE_ERRORS:
            self._roster = []
        self._fill_person_filter()
        tab = self.current_tab()
        if tab == "today":
            self._fill_today()
        elif tab == "history":
            self._load_history()
        else:
            self._fill_people()
