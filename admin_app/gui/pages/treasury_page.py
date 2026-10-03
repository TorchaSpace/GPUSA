"""Treasury & Ledger page - the real build of Treasury.dc.html, replacing
its themed placeholder.

Recreated from the mockup:
- Three KPI cards: Total receivables (Checks / Notes / Overdue), Total
  payables (Checks / Transfers / Net position), Upcoming due dates (next
  7 days: count, amount, the next three with in/out arrows).
- "Next 30 days · Payment & collection milestones": the day strip with
  collection/payment dots and a running line (components/milestone_strip.py),
  plus the hover "focus" row listing that day's documents.
- The document table with its two tabs ("Received Checks & Notes" /
  "Issued Checks & Payments", with counts), the All / Pending / Cleared /
  Overdue filter, the due-date sort, "+ Record receipt / payment", and the
  "N documents · total" footer.

All real, through database.ledger_repository; every number is computed
by shared.treasury from the stored documents. Added beyond the mockup,
because a ledger you can't settle is just a list: Mark cleared, Mark
endorsed (received checks/notes only), Reopen, Edit and Delete on the
selected row, and a Site column (depot_app records its own site's
documents from its Manager Portal).

Deliberately different from the mockup:
- The top line is the running NET of scheduled items starting at 0, not
  a "projected balance" - the mockup starts that from a made-up $1.84M
  and no cash/bank balance exists anywhere in this system.
- No "Bank reconciliation last run today, 06:00" footer - nothing
  reconciles against a bank.
- "Overdue" is computed from the due date every time the page loads, not
  stored, so it can't go stale.
- The header's search box and hardcoded "Pending approvals 4" button are
  the mockup's shared page chrome; the real approvals button lives on
  Overview.
- No currency symbol, as everywhere else in the app.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.ledger_entry_form_popup import LedgerEntryFormPopup
from admin_app.gui.components.milestone_strip import MilestoneStrip
from admin_app.gui.components.section import Section
from admin_app.gui.components.stat_card import StatCard, stat_breakdown_item
from admin_app.gui.components.styled_table import cell, styled_table
from shared.i18n import tr
from admin_app.theme import CLASSICAL_PALETTE
from database import ledger_repository, purchase_order_repository
from database.exceptions import DataAccessError
from shared.formatting import format_amount
from shared.models import LedgerEntry
from shared.treasury import (
    compact_amount,
    display_status,
    due_relative_text,
    is_overdue,
    matches_status_filter,
    milestones,
    summarize,
)

_TABS = (("in", "Received Checks & Notes"), ("out", "Issued Checks & Payments"))
_STATUS_FILTERS = ("All", "Pending", "Cleared", "Overdue")


def long_date(value: date) -> str:
    """The mockup's en-GB style: "26 Sep 2026"."""
    return f"{value.day:02d} {value.strftime('%b')} {value.year}"


def _status_color(status: str) -> str:
    p = CLASSICAL_PALETTE
    return {
        "Overdue": p["alert_critical"],
        "Cleared": p["alert_success"],
        "Endorsed": p["alert_success"],
    }.get(status, p["accent"])


class _SegmentButton(QPushButton):
    """Checkable tab/segment button in the mockup's style: muted text,
    gold text + gold underline when selected."""

    def __init__(self, label: str, parent=None):
        super().__init__(label.replace("&", "&&"), parent)  # "&" alone is a mnemonic marker
        p = CLASSICAL_PALETTE
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QPushButton {{ background: transparent; color: {p['text_secondary']}; border: none;
                border-bottom: 2px solid transparent; padding: 6px 10px; font-size: 13px; }}
            QPushButton:hover {{ color: {p['text_primary']}; }}
            QPushButton:checked {{ color: {p['accent']}; border-bottom: 2px solid {p['accent']}; }}
            """
        )


class TreasuryPage(AdminPage):
    def __init__(self, parent: QWidget | None = None, today_provider=date.today):
        super().__init__(tr("page.treasury.title"), parent, subtitle=tr("page.treasury.subtitle"))
        self._today_provider = today_provider  # injectable so tests can pin "today"
        self._entries: list[LedgerEntry] = []
        self._tab = "in"
        self._status_filter = "All"
        self._ascending = True
        self._shown: list[LedgerEntry] = []

        refresh = CompactButton(tr("admin.refresh"))
        refresh.clicked.connect(self.reload)
        self.add_header_action(refresh)

        self.body_layout().addWidget(self._build_kpi_row())
        self.body_layout().addWidget(self._build_milestones())
        self.body_layout().addWidget(self._build_documents(), stretch=1)

        self._popup = LedgerEntryFormPopup(self)
        self._popup.accepted.connect(self._save_popup)

        self.reload()

    def today(self) -> date:
        return self._today_provider()

    # --- KPI cards ---------------------------------------------------------

    def _build_kpi_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._recv_card = StatCard("Total receivables", "—")
        self._recv_items = {k: stat_breakdown_item(k, "—") for k in ("Checks", "Notes", "Overdue")}
        for item in self._recv_items.values():
            self._recv_card.footer_layout().addWidget(item)

        self._pay_card = StatCard("Total payables", "—")
        self._pay_items = {k: stat_breakdown_item(k, "—") for k in ("Checks", "Transfers", "Net position")}
        for item in self._pay_items.values():
            self._pay_card.footer_layout().addWidget(item)

        self._due_card = StatCard("Upcoming due dates", "—", corner_note="Next 7 days")
        due_list = QWidget()
        self._due_list_layout = QVBoxLayout(due_list)
        self._due_list_layout.setContentsMargins(0, 0, 0, 0)
        self._due_list_layout.setSpacing(4)
        self._due_card.footer_layout().addWidget(due_list, stretch=1)

        for card in (self._recv_card, self._pay_card, self._due_card):
            layout.addWidget(card, stretch=1)
        return row

    @staticmethod
    def _set_item(item: QWidget, text: str, color: str | None = None) -> None:
        label = item.layout().itemAt(1).widget()
        label.setText(text)
        if color:
            label.setStyleSheet(f"font-size: 13px; color: {color}; border: none;")

    # --- 30-day milestones -------------------------------------------------

    def _build_milestones(self) -> Section:
        p = CLASSICAL_PALETTE
        self._milestone_section = Section("Next 30 days", "Payment & collection milestones")
        legend = QLabel(
            f"<span style='color:{p['alert_success']}'>●</span> Collection &nbsp;&nbsp;"
            f"<span style='color:{p['alert_warning']}'>●</span> Payment &nbsp;&nbsp;"
            f"<span style='color:{p['accent']}'>—</span> Net of scheduled items"
        )
        legend.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        self._milestone_section.add_header_control(legend)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(6)
        ends = QHBoxLayout()
        self._net_start_label = QLabel("Net 0")
        self._net_end_label = QLabel()
        for label in (self._net_start_label, self._net_end_label):
            label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        ends.addWidget(self._net_start_label)
        ends.addStretch(1)
        ends.addWidget(self._net_end_label)
        layout.addLayout(ends)

        self._strip = MilestoneStrip()
        self._strip.day_hovered.connect(self._show_focus)
        layout.addWidget(self._strip)

        self._focus_label = QLabel()
        self._focus_label.setWordWrap(True)
        self._focus_label.setTextFormat(Qt.RichText)
        self._focus_label.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        layout.addWidget(self._focus_label)

        self._milestone_section.body_layout().addWidget(body)
        return self._milestone_section

    def _focus_html(self, title: str, entries: list[LedgerEntry]) -> str:
        p = CLASSICAL_PALETTE
        if not entries:
            return f"<b>{title}</b>"
        parts = []
        for entry in entries:
            sign, color = ("+", p["alert_success"]) if entry.direction == "in" else ("−", p["alert_warning"])
            parts.append(
                f"<span style='color:{color}'>{sign}{compact_amount(entry.amount)}</span> "
                f"{entry.counterparty} <span style='color:#7d7979'>{entry.type_label} {entry.doc_no}</span>"
            )
        return f"<b>{title}</b> &nbsp; " + " &nbsp;·&nbsp; ".join(parts)

    def _show_focus(self, index: int) -> None:
        days = self._strip.days()
        if 0 <= index < len(days):
            day = days[index]
            items = day.incoming + day.outgoing
            title = day.day.strftime("%A ") + f"{day.day.day} {day.day.strftime('%b')}"
            self._focus_label.setText(self._focus_html(title if items else f"{title} · nothing due", items))
            return
        upcoming = [e for d in days for e in d.incoming + d.outgoing][:4]
        self._focus_label.setText(self._focus_html("Next up", upcoming) if upcoming else "<b>Nothing scheduled in the next 30 days.</b>")

    # --- document table ------------------------------------------------------

    def _build_documents(self) -> Section:
        p = CLASSICAL_PALETTE
        section = Section("Ledger", "Checks, notes & payments")
        self._record_button = CompactButton("+ Record receipt", variant="primary")
        self._record_button.clicked.connect(self._open_record)
        section.add_header_control(self._record_button)

        toolbar = QWidget()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 8, 12, 4)
        bar.setSpacing(4)
        self._tab_group = QButtonGroup(self)
        self._tab_buttons: dict[str, _SegmentButton] = {}
        for key, label in _TABS:
            button = _SegmentButton(label)
            button.clicked.connect(lambda _c=False, k=key: self._set_tab(k))
            self._tab_group.addButton(button)
            self._tab_buttons[key] = button
            bar.addWidget(button)
        bar.addStretch(1)
        self._status_group = QButtonGroup(self)
        self._status_buttons: dict[str, _SegmentButton] = {}
        for status in _STATUS_FILTERS:
            button = _SegmentButton(status)
            button.clicked.connect(lambda _c=False, s=status: self._set_status_filter(s))
            self._status_group.addButton(button)
            self._status_buttons[status] = button
            bar.addWidget(button)
        self._tab_buttons["in"].setChecked(True)
        self._status_buttons["All"].setChecked(True)
        section.body_layout().addWidget(toolbar)

        self._table = styled_table(
            ["Document", "Counterparty", "Site", "Issue date", "Due date ↑", "Amount", "Status"]
        )
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (0, 2, 3, 4, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.sectionClicked.connect(self._on_header_clicked)
        self._table.setMinimumHeight(300)
        self._table.itemSelectionChanged.connect(self._update_actions)
        self._table.doubleClicked.connect(lambda _i: self._open_edit())
        section.body_layout().addWidget(self._table)

        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(12, 8, 12, 10)
        row.setSpacing(6)
        self._clear_button = CompactButton("Mark cleared")
        self._clear_button.clicked.connect(lambda: self._change_status("clear"))
        self._endorse_button = CompactButton("Mark endorsed")
        self._endorse_button.setToolTip("A received check or note passed on to someone else instead of cashed")
        self._endorse_button.clicked.connect(lambda: self._change_status("endorse"))
        self._reopen_button = CompactButton("Reopen")
        self._reopen_button.clicked.connect(lambda: self._change_status("reopen"))
        self._edit_button = CompactButton("Edit")
        self._edit_button.clicked.connect(self._open_edit)
        self._delete_button = CompactButton("Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        for button in (self._clear_button, self._endorse_button, self._reopen_button, self._edit_button, self._delete_button):
            row.addWidget(button)
        row.addStretch(1)
        self._message_label = QLabel()
        self._message_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        row.addWidget(self._message_label)
        section.body_layout().addWidget(actions)

        self._footer_label = QLabel()
        self._footer_label.setStyleSheet(
            f"font-size: 11px; color: {p['text_secondary']}; padding: 0 16px 10px 16px;"
        )
        section.body_layout().addWidget(self._footer_label)
        return section

    # --- data ----------------------------------------------------------------

    def reload(self) -> None:
        try:
            self._entries = ledger_repository.list_entries()
        except DataAccessError:
            self._entries = []
        today = self.today()
        p = CLASSICAL_PALETTE

        summary = summarize(self._entries, today)
        self._recv_card.set_value(compact_amount(summary.receivables_total))
        self._set_item(self._recv_items["Checks"], compact_amount(summary.receivables_checks))
        self._set_item(self._recv_items["Notes"], compact_amount(summary.receivables_notes))
        self._set_item(
            self._recv_items["Overdue"],
            compact_amount(summary.receivables_overdue),
            p["alert_critical"] if summary.receivables_overdue else None,
        )
        self._pay_card.set_value(compact_amount(summary.payables_total))
        self._set_item(self._pay_items["Checks"], compact_amount(summary.payables_checks))
        self._set_item(self._pay_items["Transfers"], compact_amount(summary.payables_transfers))
        net = summary.net_position
        self._set_item(
            self._pay_items["Net position"],
            ("+" if net > 0 else "") + compact_amount(net),
            p["alert_success"] if net >= 0 else p["alert_critical"],
        )

        self._due_card.set_value(str(len(summary.due_soon)))
        while self._due_list_layout.count():
            item = self._due_list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        caption = QLabel(f"items · {compact_amount(summary.due_soon_amount)}")
        caption.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']}; border: none;")
        self._due_list_layout.addWidget(caption)
        for entry in summary.due_soon[:3]:
            days = (entry.due_date - today).days
            when = "today" if days == 0 else "tomorrow" if days == 1 else entry.due_date.strftime("%a ") + str(entry.due_date.day)
            arrow, color = ("↙", p["alert_success"]) if entry.direction == "in" else ("↗", p["alert_warning"])
            when_color = p["alert_warning"] if days <= 1 else "#9b9797"
            line = QLabel(
                f"<span style='color:{color}'>{arrow}</span> {entry.counterparty} "
                f"<span style='float:right'>{compact_amount(entry.amount)}</span>"
                f"<span style='color:{when_color}'> · {when}</span>"
            )
            line.setStyleSheet(f"font-size: 12px; color: {p['text_primary']}; border: none;")
            self._due_list_layout.addWidget(line)

        days = milestones(self._entries, today)
        self._strip.set_days(days)
        end = days[-1]
        self._milestone_section.set_kicker(
            f"Next 30 days · {today.day} {today.strftime('%b')} – {end.day.day} {end.day.strftime('%b')}"
        )
        sign = "+" if end.cumulative_net > 0 else ""
        self._net_end_label.setText(f"{sign}{compact_amount(end.cumulative_net)} net scheduled by {end.day.day} {end.day.strftime('%b')}")
        self._show_focus(-1)

        counts = {key: sum(1 for e in self._entries if e.direction == key) for key, _ in _TABS}
        for key, label in _TABS:
            self._tab_buttons[key].setText(f"{label}  {counts[key]}".replace("&", "&&"))
        self._render_table()

    def _set_tab(self, key: str) -> None:
        self._tab = key
        self._tab_buttons[key].setChecked(True)
        self._record_button.setText("+ Record receipt" if key == "in" else "+ Record payment")
        self._render_table()

    def _set_status_filter(self, status: str) -> None:
        self._status_filter = status
        self._status_buttons[status].setChecked(True)
        self._render_table()

    def _on_header_clicked(self, column: int) -> None:
        if column == 4:
            self._ascending = not self._ascending
            self._table.horizontalHeaderItem(4).setText("Due date ↑" if self._ascending else "Due date ↓")
            self._render_table()

    def _render_table(self) -> None:
        p = CLASSICAL_PALETTE
        today = self.today()
        rows = [
            e for e in self._entries
            if e.direction == self._tab and matches_status_filter(e, self._status_filter, today)
        ]
        rows.sort(key=lambda e: (e.due_date, e.id or 0), reverse=not self._ascending)
        self._shown = rows
        self._table.setRowCount(len(rows))
        for index, entry in enumerate(rows):
            status = display_status(entry, today)
            days = (entry.due_date - today).days
            due_color = (
                p["alert_critical"] if is_overdue(entry, today)
                else p["alert_warning"] if entry.is_open and days <= 7
                else None
            )
            type_color = p["accent"] if entry.doc_type == "note" else None
            counterparty = entry.counterparty + (f"  ·  {entry.detail}" if entry.detail else "")
            values = [
                cell(f"{entry.type_label} · {entry.doc_no}", color=type_color),
                cell(counterparty),
                cell(entry.site or "Company"),
                cell(long_date(entry.issue_date)),
                cell(f"{long_date(entry.due_date)} · {due_relative_text(entry, today)}", color=due_color),
                cell(format_amount(entry.amount), right=True),
                cell(status, color=_status_color(status)),
            ]
            for column, item in enumerate(values):
                self._table.setItem(index, column, item)
        total = sum(e.amount for e in rows)
        noun = "document" if len(rows) == 1 else "documents"
        self._footer_label.setText(f"{len(rows)} {noun} · {format_amount(total)}")
        self._update_actions()

    # --- selection + actions ----------------------------------------------

    def selected_entry(self) -> LedgerEntry | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows or rows[0].row() >= len(self._shown):
            return None
        return self._shown[rows[0].row()]

    def select_entry(self, entry_id: int) -> None:
        for index, entry in enumerate(self._shown):
            if entry.id == entry_id:
                self._table.selectRow(index)
                return

    def _update_actions(self) -> None:
        entry = self.selected_entry()
        has = entry is not None
        self._edit_button.setEnabled(has)
        self._delete_button.setEnabled(has)
        self._clear_button.setEnabled(has and entry.is_open)
        self._reopen_button.setEnabled(has and not entry.is_open)
        self._endorse_button.setEnabled(
            has and entry.is_open and entry.direction == "in" and entry.doc_type in ("check", "note")
        )

    def _change_status(self, action: str) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        try:
            if action == "clear":
                updated = ledger_repository.mark_cleared(entry.id)
                message = f"{entry.type_label} {entry.doc_no} marked cleared."
            elif action == "endorse":
                updated = ledger_repository.mark_endorsed(entry.id)
                message = f"{entry.type_label} {entry.doc_no} marked endorsed."
            else:
                updated = ledger_repository.reopen(entry.id)
                message = f"{entry.type_label} {entry.doc_no} reopened."
        except (DataAccessError, ValueError) as exc:
            self._message_label.setText(f"Couldn't update: {exc}")
            return
        self.reload()
        self.select_entry(updated.id)
        self._message_label.setText(message)

    def _suggestions(self) -> tuple[list[str], list[str]]:
        try:
            names = ledger_repository.known_counterparties()
            sites = {e.site for e in self._entries if e.site}
            sites |= {o.site for o in purchase_order_repository.list_orders(limit=200)}
        except DataAccessError:
            names, sites = [], set()
        return names, sorted(sites)

    def _open_record(self) -> None:
        names, sites = self._suggestions()
        self._popup.open_or_refresh(entry=None, direction=self._tab, counterparties=names, sites=sites, today=self.today())

    def _open_edit(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            return
        names, sites = self._suggestions()
        self._popup.open_or_refresh(entry=entry, counterparties=names, sites=sites, today=self.today())

    def _save_popup(self) -> None:
        entry = self._popup.result_entry()
        try:
            saved = ledger_repository.update(entry) if self._popup.is_editing() else ledger_repository.create(entry)
        except (DataAccessError, ValueError) as exc:
            self._popup.show_error(f"Couldn't save: {exc}")
            return
        if saved.direction != self._tab:
            self._set_tab(saved.direction)
        self.reload()
        self.select_entry(saved.id)
        self._message_label.setText(f"Saved {saved.type_label.lower()} {saved.doc_no}.")

    def _confirm_delete(self, entry: LedgerEntry) -> bool:
        """Separate so tests can answer without a modal dialog."""
        answer = QMessageBox.question(
            self, "Delete document?", f"Delete {entry.type_label.lower()} {entry.doc_no}? This can't be undone."
        )
        return answer == QMessageBox.Yes

    def _delete_selected(self) -> None:
        entry = self.selected_entry()
        if entry is None or not self._confirm_delete(entry):
            return
        try:
            ledger_repository.delete(entry.id)
        except DataAccessError as exc:
            self._message_label.setText(f"Couldn't delete: {exc}")
            return
        self.reload()
        self._message_label.setText(f"Deleted {entry.type_label.lower()} {entry.doc_no}.")
