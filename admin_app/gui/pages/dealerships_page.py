"""Dealerships page - the mockup's card grid (Dealerships.dc.html).

A filter bar (region, sort, search, "showing n of m"), then one card per
dealership: name, city · code, region, revenue this month, a 7-day
sparkline with its trend and the active staff count. Clicking a card
opens its detail across the full width under that row - status, the
numbers, Edit / Reorder levels / Opening stock / Delete, the staff list
and the stock on hand (lowest against its reorder level first).

All of it is real (see components/dealership_cards.py): revenue from the
sales each till recorded with its dealership code, staff from Workforce,
stock from the dealership's own shelf. The old table + side panel showed
six text columns and a mostly empty panel; this replaces it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSizePolicy, QWidget

from admin_app.gui.components.admin_page import AdminPage
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.dealership_cards import (
    CardGrid, DealershipCard, DealershipDetail, DealershipStats, build_stats,
)
from admin_app.gui.components.dealership_form_popup import DealershipFormPopup
from admin_app.gui.components.dealership_table import status_for
from admin_app.gui.components.reorder_levels_popup import ReorderLevelsPopup
from admin_app.gui.components.stock_move_popup import StockMovePopup
from admin_app.theme import CLASSICAL_PALETTE
from database import (
    dealership_repository, employee_repository, product_repository, stock_repository, transaction_repository,
    warehouse_repository,
)
from database.exceptions import DATABASE_ERRORS
from shared import current_session
from shared.i18n import region_label, tr
from shared.models import DEALERSHIP_REGIONS, Dealership, StockLocation

_SORTS = ("revenue", "name", "region")


def _combo_style() -> str:
    p = CLASSICAL_PALETTE
    return (
        f"QComboBox {{ background-color: {p['background']}; color: {p['text_primary']}; "
        f"border: 1px solid {p['border']}; border-radius: {p['radius_md']}; padding: 6px 10px; "
        f"font-size: 13px; min-width: 170px; }}"
    )


class DealershipsPage(AdminPage):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("page.dealerships.title"), parent, subtitle=tr("page.dealerships.subtitle"))
        p = CLASSICAL_PALETTE

        self._active_pill = QLabel()
        self._active_pill.setStyleSheet(
            f"font-size: 11px; padding: 3px 12px; color: {p['accent']}; border: 1px solid {p['accent']}; border-radius: 11px;"
        )
        self._active_pill.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.add_header_action(self._active_pill)
        add_button = CompactButton(tr("admin.dealerships.add"), variant="primary")
        add_button.clicked.connect(self._open_add_popup)
        self.add_header_action(add_button)

        self._popup = DealershipFormPopup(self)
        self._popup.accepted.connect(self._save_popup)
        self._reorder_popup = ReorderLevelsPopup(self)
        self._reorder_popup.levels_changed.connect(self.reload)
        self._count_popup = StockMovePopup(self)
        self._count_popup.stock_changed.connect(self.reload)

        self._all_dealerships: list[Dealership] = []
        self._stats: dict[str, DealershipStats] = {}
        self._selected_code: str | None = None

        self.body_layout().addWidget(self._build_filter_bar())

        self._detail = DealershipDetail()
        self._detail.close_requested.connect(lambda: self.show_dealership(None))
        self._detail.edit_button.clicked.connect(self._open_edit_popup)
        self._detail.delete_button.clicked.connect(self._delete_selected)
        self._detail.levels_button.clicked.connect(self._open_reorder_levels)
        self._detail.opening_button.clicked.connect(self._open_opening_stock)
        # The names the rest of the app and the tests know these by.
        self._detail_name = self._detail.name_label
        self._detail_code = self._detail.code_label
        self._detail_rows_container = self._detail.metrics
        self._detail_edit_button = self._detail.edit_button
        self._detail_delete_button = self._detail.delete_button
        self._detail_levels_button = self._detail.levels_button
        self._detail_opening_button = self._detail.opening_button

        self._grid = CardGrid(self._detail)
        self.body_layout().addWidget(self._grid)

        self._empty = QLabel()
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']}; padding: 48px;")
        self.body_layout().addWidget(self._empty)
        self.body_layout().addStretch(1)

        self.reload()
        self._show_detail(None)

    # --- filter bar --------------------------------------------------------

    def _build_filter_bar(self) -> QWidget:
        p = CLASSICAL_PALETTE
        bar = QWidget()
        bar.setObjectName("dealerFilters")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet(f"#dealerFilters {{ border-bottom: 1px solid {p['border']}; }}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 14)
        layout.setSpacing(12)

        self._region_input = QComboBox()
        self._region_input.setStyleSheet(_combo_style())
        self._region_input.addItem(tr("admin.dealerships.all_regions"), None)
        for region in DEALERSHIP_REGIONS:
            self._region_input.addItem(region_label(region), region)
        self._region_input.currentIndexChanged.connect(self._render)
        layout.addWidget(self._region_input)

        self._sort_input = QComboBox()
        self._sort_input.setStyleSheet(_combo_style())
        for key in _SORTS:
            self._sort_input.addItem(tr(f"admin.dealerships.sort_{key}"), key)
        self._sort_input.currentIndexChanged.connect(self._render)
        layout.addWidget(self._sort_input)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("admin.dealerships.search"))
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedWidth(280)
        self._search_input.setStyleSheet(
            f"QLineEdit {{ background: transparent; color: {p['text_primary']}; border: none; "
            f"border-bottom: 1px solid {p['border']}; padding: 6px 2px; font-size: 13px; }}"
            f"QLineEdit:focus {{ border-bottom-color: {p['accent']}; }}"
        )
        self._search_input.textChanged.connect(self._render)
        layout.addWidget(self._search_input)
        layout.addStretch(1)

        self._shown_label = QLabel()
        self._shown_label.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        layout.addWidget(self._shown_label)
        return bar

    # --- data ----------------------------------------------------------------

    def reload(self) -> None:
        """Re-read dealerships, this month's sales and staff (MainWindow
        calls this each time the page is shown). The open dealership stays
        open, with fresh numbers, if it still exists."""
        try:
            self._all_dealerships = dealership_repository.list_all()
        except DATABASE_ERRORS:
            self._all_dealerships = []
        today = date.today()
        start = min(today.replace(day=1), today - timedelta(days=6))
        try:
            sales = transaction_repository.list_between(datetime.combine(start, time.min),
                                                        datetime.combine(today + timedelta(days=1), time.min))
        except (ValueError, *DATABASE_ERRORS):
            sales = []
        try:
            employees = employee_repository.list_all()
        except DATABASE_ERRORS:
            employees = []
        self._stats = build_stats(self._all_dealerships, sales, employees, today)
        active = sum(1 for d in self._all_dealerships if d.is_active)
        self._active_pill.setText(tr("admin.dealerships.active_pill").format(n=active))
        self._active_pill.setVisible(bool(self._all_dealerships))

        cards = []
        for dealership in self._all_dealerships:
            card = DealershipCard(dealership, self._stats.get(dealership.code, DealershipStats()))
            card.clicked.connect(self._toggle)
            cards.append(card)
        self._grid.set_cards(cards)
        self._render()
        self.show_dealership(self._selected_code)

    def _visible(self) -> list[Dealership]:
        region = self._region_input.currentData()
        needle = self._search_input.text().strip().casefold()
        rows = [
            d for d in self._all_dealerships
            if (region is None or d.region == region)
            and (not needle or needle in d.name.casefold() or needle in d.code.casefold() or needle in d.city.casefold())
        ]
        sort = self._sort_input.currentData()
        if sort == "revenue":
            rows.sort(key=lambda d: (-self._stats.get(d.code, DealershipStats()).revenue_month, d.name.casefold()))
        elif sort == "region":
            rows.sort(key=lambda d: (region_label(d.region).casefold(), d.name.casefold()))
        else:
            rows.sort(key=lambda d: d.name.casefold())
        return rows

    def _render(self) -> None:
        visible = self._visible()
        order = {d.code: i for i, d in enumerate(visible)}
        cards = self._grid.cards()
        cards.sort(key=lambda c: order.get(c.dealership.code, len(order)))
        for card in cards:
            card.setProperty("shown", card.dealership.code in order)
        self._grid.set_order(cards)
        if self._selected_code not in order:
            self._selected_code = None
            self._show_detail(None)
        self._grid.show_detail_after(self._selected_code)
        self._shown_label.setText(tr("admin.dealerships.showing").format(
            shown=len(visible), total=len(self._all_dealerships)))
        if not self._all_dealerships:
            self._empty.setText(tr("admin.dealerships.empty"))
        elif not visible:
            self._empty.setText(tr("admin.dealerships.no_match"))
        self._empty.setVisible(not visible)

    # --- selection ---------------------------------------------------------------

    def _toggle(self, code: str) -> None:
        self.show_dealership(None if code == self._selected_code else code)

    def selected_dealership(self) -> Dealership | None:
        return next((d for d in self._all_dealerships if d.code == self._selected_code), None)

    def select_code(self, code: str) -> bool:
        """Open `code`'s detail (case-insensitive); False if it isn't shown."""
        wanted = (code or "").strip().upper()
        match = next((d for d in self._visible() if d.code.upper() == wanted), None)
        if match is None:
            return False
        self.show_dealership(match.code)
        return True

    def card_count(self) -> int:
        return len(self._visible())

    def show_dealership(self, code: str | None) -> None:
        """Open `code`'s detail under its card, or close it (None / gone)."""
        wanted = (code or "").strip().upper()
        match = next((d for d in self._visible() if d.code.upper() == wanted), None) if wanted else None
        self._selected_code = match.code if match else None
        for card in self._grid.cards():
            card.set_selected(card.dealership.code == self._selected_code)
        self._show_detail(match)
        self._grid.show_detail_after(self._selected_code)

    def _show_detail(self, dealership: Dealership | None) -> None:
        has = dealership is not None
        for button in (self._detail_edit_button, self._detail_delete_button,
                       self._detail_levels_button, self._detail_opening_button):
            button.setEnabled(has)
        if dealership is None:
            self._detail_name.setText(tr("admin.dealerships.none_selected"))
            self._detail_code.setText(tr("admin.dealerships.click_row"))
            return
        try:
            stock = stock_repository.products_at(StockLocation.dealership(dealership.code))
        except DATABASE_ERRORS:
            stock = []
        self._detail.show_dealership(dealership, self._stats.get(dealership.code, DealershipStats()), stock,
                                     status_for(dealership))

    # --- actions -------------------------------------------------------------------

    def _open_reorder_levels(self) -> None:
        dealership = self.selected_dealership()
        if dealership is not None:
            self._reorder_popup.open_for(StockLocation.dealership(dealership.code), dealership.name)

    def _open_opening_stock(self) -> None:
        """Type what this shop really holds (the count form, already on this shop): units that were waiting
        unplaced are placed here, anything beyond is new stock."""
        dealership = self.selected_dealership()
        if dealership is None:
            return
        try:
            self._count_popup.set_choices(product_repository.list_active(), warehouse_repository.list_all(active_only=True),
                                          self._all_dealerships, source=StockLocation.dealership(dealership.code))
        except DATABASE_ERRORS:
            return
        self._count_popup.show_count()

    def _open_add_popup(self) -> None:
        self._popup.open_or_refresh(dealership=None)

    def _open_edit_popup(self) -> None:
        dealership = self.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, tr("admin.dealerships.none_selected"), tr("admin.dealerships.select_first"))
            return
        self._popup.open_or_refresh(dealership=dealership)

    def _save_popup(self) -> None:
        dealership = self._popup.result_dealership()
        try:
            if self._popup.is_editing():
                dealership_repository.update(dealership)
            else:
                dealership_repository.create(dealership)
        except (*DATABASE_ERRORS, ValueError) as exc:
            self._popup.show_error(str(exc))  # popup comes back with everything typed
            return
        self._selected_code = dealership.code
        self.reload()
        self.show_dealership(dealership.code)  # fresh detail for the one just saved

    def _delete_selected(self) -> None:
        dealership = self.selected_dealership()
        if dealership is None:
            QMessageBox.information(self, tr("admin.dealerships.none_selected"), tr("admin.dealerships.select_first"))
            return
        confirm = QMessageBox.question(
            self, tr("admin.dealerships.delete_title"), tr("admin.dealerships.delete_confirm").format(name=dealership.name)
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            dealership_repository.delete(dealership.code, current_session.actor())
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("admin.dealerships.delete_failed"), str(exc))
            return
        self._selected_code = None
        self.reload()
        self.show_dealership(None)
