"""Inline "safe purchase price range" editor - the admin mockup's
Settings > Pricing Thresholds table (Settings.dc.html), per product
because products have no category.

One row per product: name (with a one-line note on what happens to its
orders), a Min box, a range bar, a Max box, an optional default
supplier and how many orders were held in the last 30 days. Type a
number or drag the bar's handles; nothing is written until Save, and the
footer says how many products have unsaved changes. Clearing both boxes
of a product that had a range removes it (its orders are then held).
Tick marks on the bar are that product's recent requests (orange: held).

Replaces the old popup flow (pick a product in a combo, type, save; then
select a row and press Edit to change it) - every product is right here,
edits are typed in place, and Save writes them all at once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSizePolicy, QTableWidget, QVBoxLayout, QWidget,
)

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.styled_table import styled_table
from admin_app.theme import CLASSICAL_PALETTE
from shared.currency import format_money, symbol
from shared.formatting import MAX_AMOUNT, format_amount, parse_amount, parse_db_timestamp
from shared.i18n import tr
from shared.models import PriceRange, Product, PurchaseOrder

_COL_PRODUCT, _COL_MIN, _COL_BAR, _COL_MAX, _COL_SUPPLIER, _COL_HELD = range(6)
_RECENT_DAYS = 30


def _nice_ceiling(value: float) -> float:
    """The next "round" number at or above value: 1, 1.5, 2, 2.5, 3, 4, 5,
    6, 8 x 10^k - fine enough that a band never shrinks to a corner."""
    if value <= 0:
        return 10.0
    power = 10 ** math.floor(math.log10(value))
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if value <= step * power:
            return step * power
    return 10 * power


def _drag_step(scale: float) -> float:
    """What a dragged handle snaps to: about 1/100 of the bar, rounded."""
    raw = scale / 100
    power = 10 ** math.floor(math.log10(raw)) if raw > 0 else 0.01
    for step in (1, 2, 5, 10):
        if raw <= step * power:
            return max(step * power, 0.01)
    return max(10 * power, 0.01)


class RangeBar(QWidget):
    """The mockup's range slider: a track from 0 to `scale`, the safe band
    in gold with a handle at each end, and a tick for every recent
    request. Dragging a handle emits dragged(low, high); pressing on an
    empty bar starts a new band there."""

    dragged = Signal(float, float)

    _PAD = 9  # half a handle, so a handle at 0 or at the scale end stays inside

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scale = 100.0
        self._low: float | None = None
        self._high: float | None = None
        self._ticks: list[tuple[float, bool]] = []
        self._grab: str | None = None  # "low" / "high" while dragging
        self.setMinimumWidth(160)
        self.setFixedHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    # --- state -------------------------------------------------------------

    def set_scale(self, scale: float) -> None:
        self._scale = max(scale, 0.01)
        self.update()

    def scale(self) -> float:
        return self._scale

    def set_band(self, low: float | None, high: float | None) -> None:
        self._low, self._high = low, high
        self.update()

    def band(self) -> tuple[float | None, float | None]:
        return self._low, self._high

    def set_ticks(self, ticks: list[tuple[float, bool]]) -> None:
        """(unit price, was held) per recent request."""
        self._ticks = ticks
        self.update()

    # --- geometry ----------------------------------------------------------

    def _track(self) -> QRectF:
        return QRectF(self._PAD, 6, max(self.width() - 2 * self._PAD, 1), 16)

    def _x_for(self, value: float) -> float:
        track = self._track()
        return track.left() + track.width() * min(max(value / self._scale, 0.0), 1.0)

    def _value_at(self, x: float) -> float:
        track = self._track()
        fraction = min(max((x - track.left()) / track.width(), 0.0), 1.0)
        step = _drag_step(self._scale)
        return round(round(fraction * self._scale / step) * step, 2)

    # --- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = CLASSICAL_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        mid = track.center().y()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(234, 231, 231, 34))
        painter.drawRoundedRect(QRectF(track.left(), mid - 1.5, track.width(), 3), 1.5, 1.5)

        for price, held in self._ticks:
            if price > self._scale:
                continue
            x = self._x_for(price)
            painter.setPen(QPen(QColor(p["alert_warning"] if held else "#9b9797"), 1))
            painter.drawLine(QPointF(x, mid - 6), QPointF(x, mid + 6))

        if self._high is not None:
            low = self._low or 0.0
            x1, x2 = self._x_for(low), self._x_for(self._high)
            accent = QColor(p["accent"])
            painter.setPen(Qt.NoPen)
            glow = QColor(accent)
            glow.setAlpha(60)
            painter.setBrush(glow)
            painter.drawRoundedRect(QRectF(x1, mid - 3, max(x2 - x1, 1), 6), 3, 3)
            painter.setBrush(accent)
            painter.drawRect(QRectF(x1, mid - 1.5, max(x2 - x1, 1), 3))
            painter.setBrush(QColor(p["background"]))
            painter.setPen(QPen(accent, 2))
            for x in (x1, x2):
                painter.drawEllipse(QPointF(x, mid), 7, 7)

        painter.setPen(QColor("#7d7979"))
        font = painter.font()
        font.setPointSizeF(max(font.pointSizeF() - 3, 7))
        painter.setFont(font)
        bottom = QRectF(0, track.bottom() + 1, self.width(), self.height() - track.bottom() - 1)
        painter.drawText(bottom, Qt.AlignLeft | Qt.AlignTop, "0")
        painter.drawText(bottom, Qt.AlignRight | Qt.AlignTop, format_amount(self._scale).rstrip("0").rstrip(",."))
        painter.end()

    # --- dragging ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        x = event.position().x()
        value = self._value_at(x)
        if self._high is None:
            self._low, self._high, self._grab = value, value, "high"
        else:
            near_low = abs(x - self._x_for(self._low or 0.0))
            near_high = abs(x - self._x_for(self._high))
            self._grab = "low" if near_low < near_high else "high"
            self._move_to(value)
        self._emit()

    def mouseMoveEvent(self, event) -> None:
        if self._grab is None:
            return super().mouseMoveEvent(event)
        self._move_to(self._value_at(event.position().x()))
        self._emit()

    def mouseReleaseEvent(self, event) -> None:
        self._grab = None
        super().mouseReleaseEvent(event)

    def _move_to(self, value: float) -> None:
        low, high = self._low or 0.0, self._high or 0.0
        if self._grab == "low":
            low = value
            if low > high:  # crossed over: carry on as the other handle
                low, high, self._grab = high, low, "high"
        else:
            high = value
            if high < low:
                low, high, self._grab = high, low, "low"
        self._low, self._high = low, high
        self.update()

    def _emit(self) -> None:
        self.dragged.emit(self._low or 0.0, self._high or 0.0)


def _money_box(placeholder: str) -> QLineEdit:
    box = QLineEdit()
    box.setPlaceholderText(placeholder)
    box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    box.setMinimumWidth(96)
    return box


def _box_style(error: bool) -> str:
    p = CLASSICAL_PALETTE
    border = p["alert_critical"] if error else p["border"]
    return (
        f"QLineEdit {{ background: transparent; color: {p['text_primary']}; border: 1px solid {border}; "
        f"border-radius: {p['radius_md']}; padding: 4px 8px; font-size: 13px; }}"
        f"QLineEdit:focus {{ border-color: {p['alert_critical'] if error else p['accent']}; }}"
    )


@dataclass
class _Row:
    product: Product
    saved: PriceRange | None
    ticks: list[tuple[float, bool]] = field(default_factory=list)
    held: int = 0
    min_box: QLineEdit | None = None
    max_box: QLineEdit | None = None
    supplier_box: QLineEdit | None = None
    bar: RangeBar | None = None
    note: QLabel | None = None
    error: str | None = None

    def saved_texts(self) -> tuple[str, str, str]:
        if self.saved is None:
            return "", "", ""
        low = format_amount(self.saved.min_unit_price) if self.saved.min_unit_price else ""
        return low, format_amount(self.saved.max_unit_price), self.saved.default_supplier or ""

    def texts(self) -> tuple[str, str, str]:
        return self.min_box.text().strip(), self.max_box.text().strip(), self.supplier_box.text().strip()

    def is_dirty(self) -> bool:
        current, saved = self.texts(), self.saved_texts()
        if current[2] != saved[2]:
            return True
        # Compare numbers, not spelling: "520" and "520,00" are the same band.
        return any(
            (parse_amount(a) if a else None) != (parse_amount(b) if b else None) or (bool(a) != bool(b))
            for a, b in zip(current[:2], saved[:2])
        )


class PriceRangeEditor(QWidget):
    """Emits saved(message) after Save wrote something (the host reloads),
    and dirty_changed(n) when the number of unsaved products changes."""

    saved = Signal(str)
    dirty_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        p = CLASSICAL_PALETTE
        self._rows: dict[str, _Row] = {}
        self._order: list[str] = []
        self._last_saved_at: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        intro_row = QWidget()
        intro_layout = QHBoxLayout(intro_row)
        intro_layout.setContentsMargins(16, 12, 16, 12)
        intro_layout.setSpacing(12)
        self._intro = QLabel(tr("admin.purchase.ranges_intro"))
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(f"font-size: 12px; color: {p['text_secondary']};")
        intro_layout.addWidget(self._intro, stretch=1)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("admin.purchase.ranges_search"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(240)
        self._search.setStyleSheet(_box_style(False))
        self._search.textChanged.connect(self._apply_filter)
        intro_layout.addWidget(self._search, alignment=Qt.AlignTop)
        layout.addWidget(intro_row)

        money = symbol()
        unit = f" ({money})" if money else ""
        self._table: QTableWidget = styled_table([
            tr("admin.purchase.rcol_product"), tr("admin.purchase.rcol_min") + unit, tr("admin.purchase.rcol_range"),
            tr("admin.purchase.rcol_max") + unit, tr("admin.purchase.rcol_supplier"), tr("admin.purchase.rcol_held"),
        ])
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setDefaultSectionSize(56)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(_COL_PRODUCT, QHeaderView.Interactive)
        self._table.setColumnWidth(_COL_PRODUCT, 260)
        for column, width in ((_COL_MIN, 120), (_COL_MAX, 120), (_COL_SUPPLIER, 170), (_COL_HELD, 110)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self._table.setColumnWidth(column, width)
        header.setSectionResizeMode(_COL_BAR, QHeaderView.Stretch)
        layout.addWidget(self._table, stretch=1)

        footer = QWidget()
        footer.setObjectName("rangesFooter")
        footer.setAttribute(Qt.WA_StyledBackground, True)
        footer.setStyleSheet(f"#rangesFooter {{ border-top: 1px solid {p['border']}; }}")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 10, 16, 10)
        footer_layout.setSpacing(10)
        legend = QLabel(tr("admin.purchase.ranges_legend"))
        legend.setWordWrap(True)
        legend.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        footer_layout.addWidget(legend, stretch=1)
        self._status = QLabel()
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        footer_layout.addWidget(self._status)
        self._discard_button = CompactButton(tr("admin.purchase.ranges_discard"))
        self._discard_button.clicked.connect(self.discard)
        footer_layout.addWidget(self._discard_button)
        self._save_button = CompactButton(tr("admin.purchase.ranges_save"), variant="primary")
        self._save_button.clicked.connect(self.save)
        footer_layout.addWidget(self._save_button)
        layout.addWidget(footer)

        self._last_dirty = -1
        self._message: tuple[str, str] | None = None  # (text, color) of the last save, until the next edit
        self._update_status()

    # --- data ----------------------------------------------------------------

    def set_data(self, products: list[Product], ranges: list[PriceRange], orders: list[PurchaseOrder],
                 now: datetime | None = None) -> None:
        """(Re)build the rows. Unsaved typing in a row survives - a live
        reload while someone is typing must not throw their edits away."""
        drafts = {code: row.texts() for code, row in self._rows.items() if row.is_dirty()}
        bands = {r.product_barcode: r for r in ranges}
        since = (now or datetime.now(timezone.utc)) - timedelta(days=_RECENT_DAYS)
        recent: dict[str, list[PurchaseOrder]] = {}
        for order in orders:
            if order.created_at and order.status != "cancelled" and _after(order.created_at, since):
                recent.setdefault(order.product_barcode, []).append(order)

        shown = [pr for pr in products if pr.is_active or pr.barcode in bands]
        shown.sort(key=lambda pr: pr.name.casefold())
        self._rows, self._order = {}, []
        self._table.setRowCount(0)
        self._table.setRowCount(len(shown))
        for index, product in enumerate(shown):
            mine = recent.get(product.barcode, [])
            row = _Row(product, bands.get(product.barcode),
                       ticks=[(o.unit_price, o.hold_reason is not None) for o in mine],
                       held=sum(1 for o in mine if o.hold_reason is not None))
            self._build_row(index, row)
            self._rows[product.barcode] = row
            self._order.append(product.barcode)
            if product.barcode in drafts:
                low, high, supplier = drafts[product.barcode]
                for box, text in ((row.min_box, low), (row.max_box, high), (row.supplier_box, supplier)):
                    box.blockSignals(True)
                    box.setText(text)
                    box.blockSignals(False)
            self._refresh_row(row, rescale=True)
        self._apply_filter()
        self._fit_height()
        self._update_status()

    def _fit_height(self) -> None:
        """Show up to 8 rows without an inner scrollbar - the mockup's
        table is as tall as its content - and scroll beyond that."""
        rows = min(max(len(self.visible_barcodes()), 3), 8)
        header = self._table.horizontalHeader().sizeHint().height()
        self._table.setFixedHeight(header + rows * self._table.verticalHeader().defaultSectionSize() + 4)

    def _build_row(self, index: int, row: _Row) -> None:
        p = CLASSICAL_PALETTE
        name_cell = QWidget()
        name_layout = QVBoxLayout(name_cell)
        name_layout.setContentsMargins(10, 4, 6, 4)
        name_layout.setSpacing(1)
        name = QLabel(row.product.name)
        name.setStyleSheet(f"font-size: 13px; color: {p['text_primary']};")
        name.setToolTip(f"{row.product.barcode} · {row.product.name}")
        name_layout.addWidget(name)
        row.note = QLabel()
        row.note.setStyleSheet(f"font-size: 11px; color: {p['text_secondary']};")
        name_layout.addWidget(row.note)
        self._table.setCellWidget(index, _COL_PRODUCT, name_cell)

        low_text, high_text, supplier_text = row.saved_texts()
        row.min_box = _money_box("0")
        row.min_box.setText(low_text)
        row.max_box = _money_box("—")
        row.max_box.setText(high_text)
        row.supplier_box = QLineEdit(supplier_text)
        row.supplier_box.setPlaceholderText(tr("admin.purchase.p_optional"))
        row.supplier_box.setStyleSheet(_box_style(False))
        row.bar = RangeBar()
        for column, widget in ((_COL_MIN, row.min_box), (_COL_BAR, row.bar), (_COL_MAX, row.max_box),
                               (_COL_SUPPLIER, row.supplier_box)):
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(6, 8, 6, 8)
            holder_layout.addWidget(widget)
            self._table.setCellWidget(index, column, holder)

        held = QLabel(str(row.held) if row.held else "—")
        held.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        held.setContentsMargins(0, 0, 14, 0)
        held.setStyleSheet(f"font-size: 13px; color: {p['alert_warning'] if row.held else p['text_secondary']};")
        self._table.setCellWidget(index, _COL_HELD, held)

        row.min_box.textEdited.connect(lambda _t, r=row: self._on_typed(r))
        row.max_box.textEdited.connect(lambda _t, r=row: self._on_typed(r))
        row.supplier_box.textEdited.connect(lambda _t, r=row: self._on_typed(r))
        row.bar.dragged.connect(lambda low, high, r=row: self._on_dragged(r, low, high))

    # --- editing -------------------------------------------------------------

    def _on_typed(self, row: _Row) -> None:
        self._message = None
        self._refresh_row(row, rescale=False)
        self._update_status()

    def _on_dragged(self, row: _Row, low: float, high: float) -> None:
        row.min_box.setText(format_amount(low) if low > 0 else "")
        row.max_box.setText(format_amount(high))
        self._on_typed(row)

    def _parsed(self, row: _Row) -> tuple[float | None, float | None, str | None]:
        """(min, max, error) from what's typed in the row."""
        low_text, high_text, supplier = row.texts()
        low = parse_amount(low_text) if low_text else None
        high = parse_amount(high_text) if high_text else None
        if (low_text and low is None) or (high_text and high is None):
            return low, high, tr("admin.purchase.err_not_number")
        if (low is not None and low < 0) or (high is not None and high < 0):
            return low, high, tr("admin.purchase.err_not_number")
        if (low is not None and low > MAX_AMOUNT) or (high is not None and high > MAX_AMOUNT):
            return low, high, tr("admin.purchase.err_too_big")
        if high is None and (low is not None or supplier):
            return low, high, tr("admin.purchase.err_need_max")
        if high is not None and high <= 0:
            return low, high, tr("admin.purchase.p_err_max")
        if low is not None and high is not None and low > high:
            return low, high, tr("admin.purchase.p_err_minmax")
        return low, high, None

    def _refresh_row(self, row: _Row, rescale: bool) -> None:
        p = CLASSICAL_PALETTE
        low, high, error = self._parsed(row)
        row.error = error
        bad_low = error is not None and (error == tr("admin.purchase.p_err_minmax") or
                                         (bool(row.min_box.text().strip()) and low is None))
        row.min_box.setStyleSheet(_box_style(bad_low))
        row.max_box.setStyleSheet(_box_style(error is not None and not bad_low))

        if rescale or (high is not None and high > row.bar.scale()):
            reference = max([row.product.price, high or 0, low or 0] + [t for t, _h in row.ticks])
            row.bar.set_scale(_nice_ceiling(reference * 1.15))
        row.bar.set_ticks(row.ticks)
        row.bar.set_band(low if high is not None else None, high)

        dirty = row.is_dirty()
        if error:
            row.note.setText(error)
            row.note.setStyleSheet(f"font-size: 11px; color: {p['alert_critical']};")
        elif high is None:
            key = "admin.purchase.note_removing" if row.saved is not None else "admin.purchase.note_none"
            row.note.setText(tr(key))
            row.note.setStyleSheet(f"font-size: 11px; color: {p['alert_warning']};")
        else:
            row.note.setText(tr("admin.purchase.note_band").format(
                low=format_money(low or 0), high=format_money(high)))
            row.note.setStyleSheet(f"font-size: 11px; color: {p['accent'] if dirty else p['text_secondary']};")

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().casefold()
        for index, code in enumerate(self._order):
            product = self._rows[code].product
            hit = not needle or needle in product.name.casefold() or needle in product.barcode.casefold()
            self._table.setRowHidden(index, not hit)
        if self._order:
            self._fit_height()

    # --- footer ----------------------------------------------------------------

    def dirty_rows(self) -> list[_Row]:
        return [self._rows[code] for code in self._order if self._rows[code].is_dirty()]

    def _update_status(self) -> None:
        p = CLASSICAL_PALETTE
        dirty = self.dirty_rows()
        errors = [r for r in dirty if r.error]
        self._save_button.setEnabled(bool(dirty) and not errors)
        self._discard_button.setEnabled(bool(dirty))
        if errors:
            text, color = tr("admin.purchase.ranges_errors").format(n=len(errors)), p["alert_critical"]
        elif self._message:
            text, color = self._message
        elif dirty:
            text, color = tr("admin.purchase.ranges_dirty").format(n=len(dirty)), p["accent"]
        elif self._last_saved_at:
            text, color = tr("admin.purchase.ranges_saved_at").format(when=self._last_saved_at), p["text_secondary"]
        else:
            text, color = tr("admin.purchase.ranges_clean"), p["text_secondary"]
        self._status.setText(text)
        self._status.setStyleSheet(f"font-size: 12px; color: {color};")
        if len(dirty) != self._last_dirty:
            self._last_dirty = len(dirty)
            self.dirty_changed.emit(len(dirty))

    def status_text(self) -> str:
        return self._status.text()

    def discard(self) -> None:
        for row in self._rows.values():
            if row.is_dirty():
                low, high, supplier = row.saved_texts()
                row.min_box.setText(low)
                row.max_box.setText(high)
                row.supplier_box.setText(supplier)
                self._refresh_row(row, rescale=True)
        self._message = None
        self._update_status()

    def save(self) -> None:
        """Write every changed product. A product that fails keeps its
        typing (and the reason shows in the footer); the others are saved."""
        from database import purchase_order_repository
        from database.exceptions import DATABASE_ERRORS
        from shared import current_session

        p = CLASSICAL_PALETTE
        dirty = self.dirty_rows()
        if not dirty or any(r.error for r in dirty):
            return
        done, failures = 0, []
        for row in dirty:
            low, high, _error = self._parsed(row)
            supplier = row.supplier_box.text().strip() or None
            try:
                if high is None:
                    purchase_order_repository.delete_price_range(row.product.barcode, by=current_session.actor())
                    row.saved = None
                else:
                    band = PriceRange(row.product.barcode, round(low or 0.0, 2), round(high, 2), supplier)
                    purchase_order_repository.set_price_range(band, by=current_session.actor())
                    row.saved = band
                done += 1
            except (ValueError, *DATABASE_ERRORS) as exc:
                failures.append(f"{row.product.name}: {exc}")
        self._last_saved_at = datetime.now().strftime("%H:%M")
        if failures:
            self._message = (tr("admin.purchase.msg_save_failed").format(error="; ".join(failures)), p["alert_critical"])
        else:
            self._message = (tr("admin.purchase.ranges_saved").format(n=done), p["alert_success"])
        for row in dirty:
            self._refresh_row(row, rescale=False)
        self._update_status()
        if done:
            self.saved.emit(self._message[0])

    # --- test / host helpers -----------------------------------------------------

    def row_count(self) -> int:
        return len(self._order)

    def row_for(self, barcode: str) -> _Row | None:
        return self._rows.get(barcode)

    def visible_barcodes(self) -> list[str]:
        return [code for i, code in enumerate(self._order) if not self._table.isRowHidden(i)]

    def search_box(self) -> QLineEdit:
        return self._search


def _after(db_timestamp: str, since: datetime) -> bool:
    try:
        moment = parse_db_timestamp(db_timestamp)
    except (TypeError, ValueError):
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= since
