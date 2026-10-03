"""Offscreen tests: the depot's Workforce Attendance panel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import attendance_repository, employee_repository
from shared.formatting import local_clock_text, to_db_timestamp
from shared.i18n import tr
from shared.models import Employee


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def panel(qapp):
    employee_repository.create(Employee("B-7", "Elena Varga", "Operations", "Warehouse", "Merkez Depo"))
    employee_repository.create(Employee("B-8", "Off Duty", "Logistics", "Warehouse", "Merkez Depo", is_active=False))
    from depot_app.gui.attendance_panel import AttendancePanel

    widget = AttendancePanel()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _row_of(panel, badge):
    return next(r for r in range(panel._table.rowCount()) if panel._table.item(r, 0).text() == badge)


def test_a_lowercase_or_padded_badge_clocks_in_and_out(panel):
    for typed in ("b-7", "  B-7 "):
        panel._badge_input.setText(typed)
        panel._on_check_in()
        assert panel._error_label.isHidden()
        assert panel._table.item(_row_of(panel, "B-7"), 3).text() == "Present"
        panel._badge_input.setText(typed)
        panel._on_check_out()
        assert panel._table.item(_row_of(panel, "B-7"), 3).text() == "Checked out"


def test_an_inactive_employee_is_refused_with_a_message(panel):
    panel._badge_input.setText("b-8")
    panel._on_check_in()
    assert panel._error_label.isVisible() and "inactive" in panel._error_label.text()
    assert attendance_repository.list_open() == []


def test_times_are_shown_in_local_time(panel):
    attendance_repository.check_in("B-7")
    panel.reload()
    row = _row_of(panel, "B-7")
    entry = next(e for e in attendance_repository.list_roster() if e["badge_id"] == "B-7")
    assert panel._table.item(row, 4).text() == local_clock_text(entry["check_in_at"])
    assert len(panel._table.item(row, 4).text()) == 5  # "HH:MM", not the UTC "07:01:46" slice
    assert panel._table.item(row, 5).text() == "—"


def test_a_shift_left_open_since_yesterday_shows_present_with_its_date_and_a_warning(panel):
    started = datetime.now(timezone.utc) - timedelta(hours=20)
    with connection.connection_scope() as conn:
        employee_id = conn.execute("SELECT id FROM employees WHERE badge_id = 'B-7'").fetchone()[0]
        conn.execute("INSERT INTO attendance_records (employee_id, check_in_at) VALUES (?, ?)",
                     (employee_id, to_db_timestamp(started)))
    panel.reload()
    row = _row_of(panel, "B-7")
    status = panel._table.item(row, 3).text()
    assert status.startswith("Present") and tr("admin.workforce.long_open") in status
    assert panel._table.item(row, 4).text() == local_clock_text(to_db_timestamp(started))
    assert panel._on_floor_label.text().startswith("1 of")
