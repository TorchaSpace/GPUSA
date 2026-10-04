"""The Floor's Check-in Log (the mockup's left column): scan or type a badge
to check in or out, see who is on the floor, and today's punches.

Real data: database.attendance_repository (check_in / check_out /
list_roster / list_punches). Employees themselves are managed in Admin's
Workforce page; this panel only punches existing badges.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from database import attendance_repository
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.components.blueprint_frame import BlueprintFrame
from depot_app.gui.components.floor_style import (
    field_label,
    heading_label,
    info_style,
    input_style,
    notice_style,
)
from depot_app.gui.components.industry_button import IndustryButton
from depot_app.theme import INDUSTRY_PALETTE
from shared.auth import normalize_badge_id
from shared.formatting import local_clock_text
from shared.gui_kit.motion import count_up, fade_in


class _Dot(QWidget):
    """The roster's status square: filled steel-blue when on the floor, an outline when not."""

    def __init__(self, on: bool):
        super().__init__()
        p = INDUSTRY_PALETTE
        self.setFixedSize(10, 10)
        fill = p["accent"] if on else "transparent"
        edge = p["accent"] if on else "#98989b"
        self.setStyleSheet(f"background-color: {fill}; border: 1.5px solid {edge};")


class CheckInPanel(BlueprintFrame):
    attendance_changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        p = INDUSTRY_PALETTE
        super().__init__(tick_color=p["text_primary"], parent=parent)
        self.setObjectName("checkInPanel")
        self.setStyleSheet(f"#checkInPanel {{ background-color: {p['surface']}; border: 1px solid {p['border']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.addWidget(heading_label("Check-in Log", 24))
        head.addStretch(1)
        self._count = QLabel()
        self._count.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
        head.addWidget(self._count)
        layout.addLayout(head)

        layout.addWidget(field_label("Badge ID"))
        self._badge_input = QLineEdit()
        self._badge_input.setPlaceholderText("Scan badge")
        self._badge_input.setMinimumHeight(54)
        self._badge_input.setStyleSheet(input_style(22))
        self._badge_input.returnPressed.connect(self._on_check_in)
        self._badge_input.textEdited.connect(lambda _t: self._message.hide())
        layout.addWidget(self._badge_input)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self._in_button = IndustryButton("Check in", variant="accent", height=48, font_px=16)
        self._in_button.clicked.connect(self._on_check_in)
        self._out_button = IndustryButton("Check out", variant="ghost", height=48, font_px=16)
        self._out_button.clicked.connect(self._on_check_out)
        buttons.addWidget(self._in_button, stretch=1)
        buttons.addWidget(self._out_button, stretch=1)
        layout.addLayout(buttons)

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.hide()
        layout.addWidget(self._message)

        layout.addWidget(self._section_title("Roster"))
        self._roster_host = QWidget()
        self._roster_host.setStyleSheet("background: transparent;")
        self._roster_layout = QVBoxLayout(self._roster_host)
        self._roster_layout.setContentsMargins(0, 0, 0, 0)
        self._roster_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setWidget(self._roster_host)
        scroll.setMinimumHeight(150)
        layout.addWidget(scroll, stretch=2)

        layout.addWidget(self._section_title("Today's punches"))
        self._punch_host = QWidget()
        self._punch_host.setStyleSheet("background: transparent;")
        self._punch_layout = QVBoxLayout(self._punch_host)
        self._punch_layout.setContentsMargins(0, 0, 0, 0)
        self._punch_layout.setSpacing(5)
        layout.addWidget(self._punch_host)
        layout.addStretch(0)

        self.reload()

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet(
            f"font-size: 11px; letter-spacing: 1.4px; font-weight: 600; color: {INDUSTRY_PALETTE['text_secondary']};"
        )
        return label

    # -- punching ---------------------------------------------------------

    def _punch(self, action) -> None:
        badge_id = normalize_badge_id(self._badge_input.text())
        if not badge_id:
            self._say("Scan or enter a badge ID first.", ok=False)
            return
        try:
            action(badge_id)
        except DATABASE_ERRORS as exc:
            self._say(str(exc), ok=False)
            return
        self._badge_input.clear()
        self._badge_input.setFocus()
        self.reload()
        self.attendance_changed.emit()

    def _on_check_in(self) -> None:
        self._punch(self._do_in)

    def _on_check_out(self) -> None:
        self._punch(self._do_out)

    def _do_in(self, badge_id: str) -> None:
        attendance_repository.check_in(badge_id)
        self._say(f"Badge {badge_id} checked in at {datetime.now().strftime('%H:%M')}.", ok=True)

    def _do_out(self, badge_id: str) -> None:
        attendance_repository.check_out(badge_id)
        self._say(f"Badge {badge_id} checked out at {datetime.now().strftime('%H:%M')}.", ok=True)

    def _say(self, text: str, ok: bool) -> None:
        self._message.setStyleSheet(info_style() if ok else notice_style())
        self._message.setText(text)
        self._message.show()
        fade_in(self._message)

    # -- showing ----------------------------------------------------------

    def reload(self) -> None:
        try:
            roster = attendance_repository.list_roster()
            punches = attendance_repository.list_punches(limit=8)
        except DATABASE_ERRORS:
            roster, punches = [], []
        # Inactive people stay off the floor list unless they are still checked in.
        roster = [r for r in roster if r["is_active"] or r["status"] == "Present"]
        on_floor = sum(1 for r in roster if r["status"] == "Present")
        count_up(self._count, f"{on_floor} / {len(roster)} on floor")
        self._fill_roster(roster)
        self._fill_punches(punches)

    @staticmethod
    def _clear(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _fill_roster(self, roster: list[dict]) -> None:
        p = INDUSTRY_PALETTE
        self._clear(self._roster_layout)
        if not roster:
            empty = QLabel("No staff yet. Add employees in Admin > Personnel.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']}; padding: 10px 0;")
            self._roster_layout.addWidget(empty)
        for entry in roster:
            on = entry["status"] == "Present"
            row = QWidget()
            row.setObjectName("rosterRow")
            row.setAttribute(Qt.WA_StyledBackground, True)
            row.setStyleSheet(f"#rosterRow {{ background: transparent; border-bottom: 1px solid {p['border']}; }}")
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 9, 0, 9)
            line.setSpacing(10)
            line.addWidget(_Dot(on), alignment=Qt.AlignVCenter)
            names = QVBoxLayout()
            names.setSpacing(0)
            name = QLabel(entry["name"])
            name.setStyleSheet(f"font-size: 15px; font-weight: 500; color: {p['text_primary']}; border: none;")
            role = QLabel(f"#{entry['badge_id']} · {entry['role']}")
            role.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; border: none;")
            names.addWidget(name)
            names.addWidget(role)
            line.addLayout(names, stretch=1)
            state = QVBoxLayout()
            state.setSpacing(0)
            label = {"Present": "ON FLOOR", "Checked out": "CHECKED OUT"}.get(entry["status"], "OFF")
            status = QLabel(label)
            status.setAlignment(Qt.AlignRight)
            status.setStyleSheet(
                f"font-size: 12px; font-weight: 700; border: none; "
                f"color: {p['accent'] if on else p['text_primary']};"
            )
            since = QLabel(local_clock_text(entry["check_in_at"]) if on else "—")
            since.setAlignment(Qt.AlignRight)
            since.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']}; border: none;")
            state.addWidget(status)
            state.addWidget(since)
            line.addLayout(state)
            self._roster_layout.addWidget(row)
        self._roster_layout.addStretch(1)

    def _fill_punches(self, punches: list[dict]) -> None:
        p = INDUSTRY_PALETTE
        self._clear(self._punch_layout)
        if not punches:
            empty = QLabel("No punches yet today.")
            empty.setStyleSheet(f"font-size: 13px; color: {p['text_secondary']};")
            self._punch_layout.addWidget(empty)
        for punch in punches:
            line = QHBoxLayout()
            line.setSpacing(8)
            when = QLabel(local_clock_text(punch["at"]))
            when.setFixedWidth(48)
            when.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']};")
            who = QLabel(punch["name"])
            who.setStyleSheet(f"font-size: 14px; color: {p['text_primary']};")
            action = QLabel(punch["action"])
            action.setAlignment(Qt.AlignRight)
            action.setFixedWidth(40)
            action.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {p['accent_900'] if punch['action'] == 'IN' else p['text_primary']};"
            )
            line.addWidget(when)
            line.addWidget(who, stretch=1)
            line.addWidget(action)
            host = QWidget()
            host.setStyleSheet("background: transparent;")
            host.setLayout(line)
            self._punch_layout.addWidget(host)
