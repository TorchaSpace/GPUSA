"""Admin's live activity: the notification centre, its panel and toasts.

* NotificationCenter - polls database.activity_repository when the window's
  DataWatcher says the database changed, hands out the NEW events, and keeps
  an unread count (notice-or-worse events since this administrator last
  marked them read; remembered per badge in the settings table).
* fill_activity_table - one renderer for a list of events (a coloured dot,
  what happened, where/who, how long ago), used by the panel and by
  Overview's "Live activity" block.
* NotificationPanel - the bell's popup: a short summary of what the tills and
  depots just did, "Important only" or everything, and "Mark all read".
* ToastHost - small cards in the window's bottom-right corner for new events
  that need a look (warning and urgent), gone after a few seconds.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.styled_table import styled_table
from admin_app.theme import CLASSICAL_PALETTE
from database import activity_repository, settings_repository
from database.exceptions import DATABASE_ERRORS
from shared.activity import ago_text, describe, source_label
from shared.i18n import tr
from shared.models import ActivityEvent

BELL_SEVERITY = "notice"  # the unread count and the "important" filter start here
TOAST_SEVERITY_RANK = 2  # warning and urgent get a toast
MAX_TOASTS = 3
TOAST_MS = 7000


def severity_color(severity: str) -> str:
    p = CLASSICAL_PALETTE
    return {"info": p["text_secondary"], "notice": p["accent"], "warning": p["alert_warning"],
            "critical": p["alert_critical"]}.get(severity, p["text_secondary"])


class NotificationCenter(QObject):
    unread_changed = Signal(int)
    events_arrived = Signal(list)  # the new ActivityEvents, oldest first

    def __init__(self, badge_id: str | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self._badge = badge_id
        self._cursor = 0  # newest event id we have looked at
        self._read = 0  # newest event id marked read
        self._unread = 0

    # --- state -------------------------------------------------------------

    @property
    def unread(self) -> int:
        return self._unread

    def _key(self) -> str:
        return f"notifications.read.{self._badge or 'admin'}"

    def start(self) -> None:
        """Begin at 'now': what happened before this window opened is not news, but
        unread items from earlier sessions still count."""
        try:
            self._cursor = activity_repository.latest_id()
            stored = settings_repository.get_all().get(self._key())
            self._read = int(stored) if stored and stored.isdigit() else self._cursor
        except DATABASE_ERRORS:
            return
        self._recount()

    def set_badge(self, badge_id: str | None) -> None:
        self._badge = badge_id
        self.start()

    def _recount(self) -> None:
        try:
            unread = activity_repository.count_since(self._read, BELL_SEVERITY)
        except DATABASE_ERRORS:
            return
        if unread != self._unread:
            self._unread = unread
            self.unread_changed.emit(unread)

    # --- polling -------------------------------------------------------------

    def poll(self) -> list[ActivityEvent]:
        try:
            events = activity_repository.list_since(self._cursor)
        except DATABASE_ERRORS:
            return []
        if events:
            self._cursor = events[-1].id
            self._recount()
            self.events_arrived.emit(events)
        return events

    def mark_all_read(self) -> None:
        self._read = self._cursor
        try:
            settings_repository.set_many({self._key(): str(self._read)})
        except DATABASE_ERRORS:
            pass
        self._recount()

    def recent(self, limit: int = 30, important_only: bool = False) -> list[ActivityEvent]:
        try:
            return activity_repository.list_recent(limit, BELL_SEVERITY if important_only else "info")
        except DATABASE_ERRORS:
            return []


def fill_activity_table(table: QTableWidget, events: list[ActivityEvent]) -> None:
    """Rows: ●, what happened (two lines: the sentence and the detail), source · how long ago."""
    p = CLASSICAL_PALETTE
    table.setRowCount(len(events))
    for row, event in enumerate(events):
        title, detail = describe(event)
        dot = QTableWidgetItem("●")
        dot.setForeground(QColor(severity_color(event.severity)))
        dot.setTextAlignment(Qt.AlignCenter)
        what = QTableWidgetItem(f"{title}\n{detail}" if detail else title)
        what.setToolTip(f"{title}\n{detail}".strip())
        when = QTableWidgetItem(f"{source_label(event.source)} · {ago_text(event.at)}")
        when.setForeground(QColor(p["text_secondary"]))
        for column, item in enumerate((dot, what, when)):
            table.setItem(row, column, item)
        table.setRowHeight(row, 46 if detail else 32)


def activity_table() -> QTableWidget:
    table = styled_table(["", tr("activity.col_event"), tr("activity.col_when")])
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.Fixed)
    table.setColumnWidth(0, 28)
    header.setSectionResizeMode(1, QHeaderView.Stretch)
    header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
    table.setWordWrap(True)
    table.setSelectionMode(QTableWidget.NoSelection)
    return table


class NotificationPanel(QDialog):
    """The bell's popup. Non-modal: it stays up while the page behind it live-updates."""

    def __init__(self, center: NotificationCenter, parent: QWidget | None = None):
        super().__init__(parent)
        self._center = center
        p = CLASSICAL_PALETTE
        self.setWindowTitle(tr("activity.panel_title"))
        self.setWindowFlag(Qt.Tool, True)
        self.setModal(False)
        self.resize(520, 560)
        self.setStyleSheet(f"QDialog {{ background-color: {p['background']}; }} "
                           f"QLabel, QCheckBox {{ color: {p['text_primary']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel(tr("activity.panel_title"))
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {p['text_primary']};")
        head.addWidget(title)
        head.addStretch(1)
        self.important_box = QCheckBox(tr("activity.important_only"))
        self.important_box.setChecked(True)
        self.important_box.toggled.connect(self.refresh)
        head.addWidget(self.important_box)
        self.read_button = CompactButton(tr("activity.mark_read"))
        self.read_button.clicked.connect(self._mark_read)
        head.addWidget(self.read_button)
        layout.addLayout(head)
        self.table = activity_table()
        layout.addWidget(self.table, stretch=1)
        self.empty_label = QLabel(tr("activity.empty"))
        self.empty_label.setStyleSheet(f"color: {p['text_secondary']}; font-size: 12px;")
        layout.addWidget(self.empty_label)
        center.events_arrived.connect(lambda _events: self.isVisible() and self.refresh())
        self.refresh()

    def refresh(self) -> None:
        events = self._center.recent(40, self.important_box.isChecked())
        fill_activity_table(self.table, events)
        self.empty_label.setVisible(not events)

    def _mark_read(self) -> None:
        self._center.mark_all_read()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()


class ToastHost(QObject):
    """Corner cards for new events. Parented to the window so they close with it."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._toasts: list[QFrame] = []

    def show_event(self, event: ActivityEvent) -> QFrame:
        p = CLASSICAL_PALETTE
        title, detail = describe(event)
        card = QFrame(self._window)
        card.setObjectName("toast")
        card.setStyleSheet(
            f"#toast {{ background-color: {p['surface_raised']}; border: 1px solid {severity_color(event.severity)}; "
            f"border-left: 4px solid {severity_color(event.severity)}; border-radius: 4px; }} "
            f"QLabel {{ background: transparent; border: none; }}")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        head = QLabel(title)
        head.setWordWrap(True)
        head.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {p['text_primary']};")
        layout.addWidget(head)
        if detail:
            sub = QLabel(detail)
            sub.setWordWrap(True)
            sub.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
            layout.addWidget(sub)
        card.setFixedWidth(340)
        card.adjustSize()
        card.show()
        card.raise_()
        self._toasts.append(card)
        while len(self._toasts) > MAX_TOASTS:
            self._dismiss(self._toasts[0])
        self._layout()
        QTimer.singleShot(TOAST_MS, lambda c=card: self._dismiss(c))
        return card

    def _dismiss(self, card: QFrame) -> None:
        if card in self._toasts:
            self._toasts.remove(card)
            card.hide()
            card.deleteLater()
            self._layout()

    def _layout(self) -> None:
        margin, gap = 18, 8
        y = self._window.height() - margin
        for card in reversed(self._toasts):
            card.adjustSize()
            y -= card.height()
            card.move(self._window.width() - card.width() - margin, y)
            card.raise_()
            y -= gap

    def active(self) -> list[QFrame]:
        return list(self._toasts)
