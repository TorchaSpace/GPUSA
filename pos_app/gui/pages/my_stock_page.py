"""My Local Stock screen - recreates the mockup's stock list: filter
segment (All/Low/Out, a coloured dot and a live count each), then one row
per product (tint circle, name, SKU, on-hand qty, a level bar and a status
pill).

Fully real - database.stock_repository.products_at(this dealership), no placeholder
data. The one thing invented rather than read from the database is the
level bar's percentage: the mockup implies some per-product "capacity",
which Product (see shared.models) has no field for, so the bar here is
a fixed heuristic (on-hand relative to 2x the reorder point) purely for
a visual sense of "how full", not a real capacity metric - see
_fill_pct()'s docstring.

Motion (skipped when `animations_enabled()` is False): the filter's raised
highlight slides from All to Low to Out; the rows rise in one after another
whenever the list is (re)built; each on-hand number counts up from the value
that row showed before (from zero the first time) and each level bar grows to
its percentage.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, QTimer, QVariantAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from database import stock_repository
from database.exceptions import DATABASE_ERRORS
from pos_app.gui.product_status import stock_status
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.gui_kit.motion import Level, animations_enabled, count_up, stagger_in
from shared.i18n import tr
from shared.models import UNASSIGNED, Product, StockLocation
from shared.textcase import upper
from shared.warehousing import tr_or

_FILTER_KEYS = {"All": "pos.stock.filter_all", "Low": "pos.stock.filter_low", "Out": "pos.stock.filter_out"}

_RED = "#d8412f"
_YELLOW = "#f2c230"

_STATUS_META = {
    # status -> (row background, badge bg, badge fg, badge text KEY - tr() at render time, qty colour or None, bar colour)
    "out": ("#fbe3de", _RED, "white", "pos.stock.status_out", "#b3301f", _RED),
    "low": ("#fdf3cf", _YELLOW, "#3a2a05", "pos.stock.status_low", None, _YELLOW),
    "ok": (ORGANIC_PALETTE["surface_raised"], "#e1eecc", "#3d472b", "pos.stock.status_ok", None, ORGANIC_PALETTE["accent_2"]),
}

# The mockup's TINTS cycle for the circle behind each product's initial:
# accent-200, accent-2-200, neutral-300, accent-300, accent-2-300.
_TINTS = ("#f3d9c6", "#e1eecc", "#dcd3c4", "#eab894", "#c3d3a6")

# Column widths of the mockup's grid (72px 1fr 120px 96px 220px 150px, gap 16).
_COL_AVATAR, _COL_SKU, _COL_QTY, _COL_LEVEL, _COL_STATUS = 72, 120, 96, 220, 150

_CLEAR = "background: transparent; border: none;"


def _fill_pct(product: Product) -> int:
    """Heuristic-only visual fill: on-hand relative to 2x the reorder
    point. Product has no real capacity field - see module docstring."""
    denominator = max(product.critical_stock_level * 2, 1)
    return max(0, min(100, round(product.stock_quantity / denominator * 100)))


def _label(text: str, css: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(f"{_CLEAR} {css}")
    return label


class _FilterSegment(QWidget):
    """The All / Low / Out pill: a surface-coloured track with ONE raised
    highlight that slides to whichever filter is picked (PillNav's approach).
    Each item shows a coloured dot, its label and its live count."""

    picked = Signal(str)

    _PAD, _DOT, _GAP, _TRACK, _CELL_GAP, _CELL_H = 20, 12, 10, 6, 6, 52

    def __init__(self, items: list[tuple[str, str, str]], parent: QWidget | None = None):
        """items: (key, label, dot colour)."""
        super().__init__(parent)
        self.setObjectName("clear")
        self._keys = [key for key, _label_text, _dot in items]
        self._labels = {key: text for key, text, _dot in items}
        self._dots = {key: dot for key, _text, dot in items}
        self._counts = {key: 0 for key in self._keys}
        self._active = 0
        self._hover_index = -1
        self._hover = [Level(self, lambda _v: self.update(), 140) for _ in items]
        self._font = QFont(self.font())
        self._font.setPixelSize(16)
        self._font.setWeight(QFont.Bold)
        self._metrics = QFontMetrics(self._font)
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(300)
        self._slide.setEasingCurve(QEasingCurve.OutBack)
        self._slide.valueChanged.connect(self._on_slide)
        self._x, self._w = 0.0, 0.0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(self._CELL_H + 2 * self._TRACK)
        self._relayout()

    # -- geometry ----------------------------------------------------------------
    def _count_text(self, key: str) -> str:
        return str(self._counts[key])

    def _cell_width(self, index: int) -> float:
        key = self._keys[index]
        return (
            2 * self._PAD + self._DOT + self._GAP
            + self._metrics.horizontalAdvance(self._labels[key])
            + self._GAP + self._metrics.horizontalAdvance(self._count_text(key))
        )

    def _total_width(self) -> int:
        cells = sum(self._cell_width(i) for i in range(len(self._keys)))
        return int(cells + self._CELL_GAP * (len(self._keys) - 1) + 2 * self._TRACK + 1)

    def _cell(self, index: int) -> tuple[float, float]:
        x = self._TRACK + sum(self._cell_width(i) + self._CELL_GAP for i in range(index))
        return x, self._cell_width(index)

    def _relayout(self) -> None:
        """Sizes changed (a count grew a digit): resize and put the highlight back under its item."""
        self._slide.stop()
        self.setFixedWidth(self._total_width())
        self._x, self._w = self._cell(self._active)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._total_width(), self._CELL_H + 2 * self._TRACK)

    # -- state -------------------------------------------------------------------
    def active_key(self) -> str:
        return self._keys[self._active]

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts.update({key: int(value) for key, value in counts.items() if key in self._counts})
        self._relayout()

    def set_active(self, key: str, animate: bool = True) -> None:
        if key not in self._keys:
            return
        index = self._keys.index(key)
        if index == self._active and abs(self._x - self._cell(index)[0]) < 0.5:
            return
        self._active = index
        x, w = self._cell(index)
        if not animate or not animations_enabled():
            self._slide.stop()
            self._x, self._w = x, w
            self.update()
            return
        self._slide.stop()
        self._slide.setStartValue(QPointF(self._x, self._w))
        self._slide.setEndValue(QPointF(x, w))
        self._slide.start()

    def _on_slide(self, value) -> None:
        self._x, self._w = value.x(), value.y()
        self.update()

    # -- mouse -------------------------------------------------------------------
    def _index_at(self, x: float) -> int:
        for i in range(len(self._keys)):
            cx, cw = self._cell(i)
            if cx <= x <= cx + cw:
                return i
        return -1

    def mouseMoveEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if index != self._hover_index:
            if self._hover_index >= 0:
                self._hover[self._hover_index].go(0.0)
            self._hover_index = index
            if index >= 0:
                self._hover[index].go(1.0)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_index >= 0:
            self._hover[self._hover_index].go(0.0)
        self._hover_index = -1
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        index = self._index_at(event.position().x())
        if index >= 0 and event.button() == Qt.LeftButton:
            key = self._keys[index]
            self.set_active(key)
            self.picked.emit(key)
        super().mouseReleaseEvent(event)

    # -- paint -------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = ORGANIC_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["surface"]))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        cell_h = track.height() - 2 * self._TRACK
        for i, key in enumerate(self._keys):  # a faint wash under the item the mouse is over
            hover = self._hover[i].value
            if hover > 0.01 and i != self._active:
                x, w = self._cell(i)
                painter.setBrush(QColor(46, 43, 37, int(16 * hover)))
                painter.drawRoundedRect(QRectF(x, self._TRACK, w, cell_h), cell_h / 2, cell_h / 2)
        pill = QRectF(self._x, self._TRACK, self._w, cell_h)  # the raised highlight: soft shadow, then the pill
        painter.setBrush(QColor(46, 43, 37, 30))
        painter.drawRoundedRect(pill.translated(0, 1.5), cell_h / 2, cell_h / 2)
        painter.setBrush(QColor(p["surface_raised"]))
        painter.drawRoundedRect(pill, cell_h / 2, cell_h / 2)
        painter.setFont(self._font)
        for i, key in enumerate(self._keys):
            x, _w = self._cell(i)
            cursor = x + self._PAD
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._dots[key]))
            painter.drawEllipse(QPointF(cursor + self._DOT / 2, self._TRACK + cell_h / 2), self._DOT / 2, self._DOT / 2)
            cursor += self._DOT + self._GAP
            label_w = self._metrics.horizontalAdvance(self._labels[key])
            painter.setPen(QColor(p["text_primary"]))
            painter.drawText(QRectF(cursor, self._TRACK, label_w + 2, cell_h), Qt.AlignVCenter | Qt.AlignLeft, self._labels[key])
            cursor += label_w + self._GAP
            count_w = self._metrics.horizontalAdvance(self._count_text(key))
            painter.setPen(QColor(p["text_secondary"]))
            painter.drawText(QRectF(cursor, self._TRACK, count_w + 2, cell_h), Qt.AlignVCenter | Qt.AlignLeft, self._count_text(key))


class _LevelBar(QWidget):
    """The 12px level bar: neutral-300 track, a coloured fill that GROWS from its
    previous percentage to its new one (a Level tween) when `play()` is called."""

    def __init__(self, colour: str, from_pct: int, to_pct: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("clear")
        self._colour = colour
        self._from, self._to = float(from_pct), float(to_pct)
        self.setFixedSize(_COL_LEVEL, 12)
        # Fully drawn from the start unless there is something to animate.
        self._level = Level(self, lambda _v: self.update(), 650, 1.0 if from_pct == to_pct else 0.0)

    def play(self) -> None:
        if self._level.value < 1.0:
            self._level.go(1.0)

    def current_pct(self) -> float:
        return self._from + (self._to - self._from) * self._level.value

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ORGANIC_PALETTE["border"]))
        painter.drawRoundedRect(rect, 6, 6)
        width = rect.width() * self.current_pct() / 100.0
        if width > 0.5:
            clip = QPainterPath()
            clip.addRoundedRect(rect, 6, 6)
            painter.setClipPath(clip)
            painter.setBrush(QColor(self._colour))
            radius = min(6.0, width / 2)
            painter.drawRoundedRect(QRectF(0, 0, width, rect.height()), radius, radius)


class _StockRow(QWidget):
    """One product: tint circle, name + reorder note, SKU, on-hand number,
    level bar, status pill - on a radius-26 tile tinted by its status."""

    def __init__(self, product: Product, tint: str, shown_qty: int, from_pct: int, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        status = stock_status(product)
        self._bg, badge_bg, badge_fg, badge_key, qty_color, bar_color = _STATUS_META[status]
        self.setObjectName("stockRow")
        self._qty = product.stock_quantity

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(16)

        holder = QWidget()
        holder.setObjectName("clear")
        holder.setFixedWidth(_COL_AVATAR)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        avatar = QLabel(upper(product.name[:1]))
        avatar.setFixedSize(52, 52)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background-color: {tint}; border: none; border-radius: 26px; font-family: {FONT_HEADING_CSS}; "
            f"font-size: 22px; color: {p['text_primary']};"
        )
        holder_layout.addWidget(avatar, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(holder)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_text = product.name if product.is_active else f"{product.name} ({tr_or('admin.inactive_badge', 'inactive')})"
        names.addWidget(_label(
            name_text,
            f"font-weight: 700; font-size: 18px; color: {p['text_primary'] if product.is_active else p['text_secondary']};",
        ))
        reorder = (
            tr("pos.stock.reorder_at").format(n=product.critical_stock_level)
            if product.critical_stock_level else tr("pos.stock.no_reorder")
        )
        names.addWidget(_label(reorder, f"font-size: 14px; color: {p['text_secondary']};"))
        layout.addLayout(names, stretch=1)

        sku = _label(product.barcode, f"font-size: 15px; color: {p['text_secondary']};")
        sku.setFixedWidth(_COL_SKU)
        layout.addWidget(sku)

        self.qty_label = _label(
            str(shown_qty),
            f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 28px; color: {qty_color or p['text_primary']};",
        )
        self.qty_label.setFixedWidth(_COL_QTY)
        self.qty_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.qty_label)

        self.bar = _LevelBar(bar_color, from_pct, _fill_pct(product))
        layout.addWidget(self.bar, 0, Qt.AlignVCenter)

        badge = _label(
            tr(badge_key),
            f"background-color: {badge_bg}; color: {badge_fg}; border-radius: 17px; "
            f"font-weight: 800; font-size: 15px; padding: 9px 16px;",
        )
        badge.setAlignment(Qt.AlignCenter)
        badge_holder = QWidget()
        badge_holder.setObjectName("clear")
        badge_holder.setFixedWidth(_COL_STATUS)
        badge_layout = QHBoxLayout(badge_holder)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.addStretch(1)
        badge_layout.addWidget(badge)
        layout.addWidget(badge_holder)

    def play(self) -> None:
        """Count the on-hand number up to its value and grow the bar."""
        count_up(self.qty_label, str(self._qty), 520)
        self.bar.play()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._bg))
        painter.drawRoundedRect(QRectF(self.rect()), 26, 26)


class MyStockPage(QWidget):
    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._location = location  # this terminal's dealership shelf
        p = ORGANIC_PALETTE
        self.setObjectName("myStockPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Scoped rules only: a selector-less sheet here would paint a box behind every label.
        self.setStyleSheet(
            f"#myStockPage {{ background-color: {p['background']}; }} "
            f"QWidget#clear, QWidget#stockRow {{ background: transparent; }}"
        )

        self._all_products: list[Product] = []
        self._active_filter = "All"
        self._last: dict[str, tuple[int, int]] = {}  # barcode -> (qty, pct) the rows last showed
        self._rows: list[_StockRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 0)
        outer.setSpacing(18)

        outer.addWidget(self._build_header())

        self._columns_header = self._build_columns_header()
        outer.addWidget(self._columns_header)

        rows_host = QWidget()
        rows_host.setObjectName("clear")
        self._rows_layout = QVBoxLayout(rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 28)
        self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setObjectName("clear")
        scroll.setWidget(rows_host)
        outer.addWidget(scroll, stretch=1)

        self.reload(play=False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if event.spontaneous():  # restored from minimised: nothing arrived, don't replay
            return
        # Arriving on the page: the rows rise in and the numbers count up from zero.
        self._last.clear()
        self._render_rows(play=True)

    def _build_header(self) -> QWidget:
        p = ORGANIC_PALETTE
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._subtitle_label = _label("", f"font-size: 15px; color: {p['text_secondary']};")
        titles.addWidget(self._subtitle_label)
        title = _label(
            tr("pos.stock.title"),
            f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 40px; color: {p['text_primary']};",
        )
        titles.addWidget(title)
        titles_widget = QWidget()
        titles_widget.setObjectName("clear")
        titles_widget.setLayout(titles)
        row.addWidget(titles_widget)
        row.addStretch(1)

        dots = {"All": p["accent_2"], "Low": _YELLOW, "Out": _RED}
        self._filter = _FilterSegment([(key, tr(_FILTER_KEYS[key]), dots[key]) for key in ("All", "Low", "Out")])
        self._filter.picked.connect(self._set_filter)
        row.addWidget(self._filter, 0, Qt.AlignBottom)

        container = QWidget()
        container.setObjectName("clear")
        container.setLayout(row)
        return container

    def _build_columns_header(self) -> QWidget:
        p = ORGANIC_PALETTE
        row = QHBoxLayout()
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(16)
        for column, width, align in (
            ("", _COL_AVATAR, Qt.AlignLeft), ("product", 0, Qt.AlignLeft), ("sku", _COL_SKU, Qt.AlignLeft),
            ("on_hand", _COL_QTY, Qt.AlignRight), ("level", _COL_LEVEL, Qt.AlignLeft), ("status", _COL_STATUS, Qt.AlignRight),
        ):
            label = _label(
                upper(tr(f"pos.stock.col_{column}")) if column else "",
                f"font-size: 13px; font-weight: 700; letter-spacing: 1px; color: {p['text_secondary']};",
            )
            label.setAlignment(align | Qt.AlignVCenter)
            if width:
                label.setFixedWidth(width)
                row.addWidget(label)
            else:
                row.addWidget(label, stretch=1)
        container = QWidget()
        container.setObjectName("clear")
        container.setLayout(row)
        return container

    def _set_filter(self, key: str) -> None:
        self._active_filter = key
        self._filter.set_active(key)
        self._render_rows(play=True)

    def reload(self, play: bool = True) -> None:
        try:
            here = stock_repository.products_at(self._location)
        except DATABASE_ERRORS:
            here = []
        # "My stock" is what this shelf carries: products it holds or has held. The rest of the
        # catalogue isn't "out of stock" here - it was never stocked - and would swamp the Out count.
        self._all_products = [p for p in here if p.stocked_here or p.stock_quantity > 0]
        self._subtitle_label.setText(tr("pos.stock.counted").format(time=datetime.now().strftime("%H:%M")))
        self._render_filter_labels()
        self._render_rows(play=play)

    def _render_filter_labels(self) -> None:
        counts = {"All": len(self._all_products), "Low": 0, "Out": 0}
        for product in self._all_products:
            status = stock_status(product)
            if status == "low":
                counts["Low"] += 1
            elif status == "out":
                counts["Out"] += 1
        self._filter.set_counts(counts)
        self._filter.set_active(self._active_filter, animate=False)

    def _render_rows(self, play: bool = True) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self._rows = []

        if self._active_filter == "All":
            visible = self._all_products
        else:
            wanted = "low" if self._active_filter == "Low" else "out"
            visible = [product for product in self._all_products if stock_status(product) == wanted]

        animate = play and self.isVisible() and animations_enabled()
        tints = {product.barcode: _TINTS[index % len(_TINTS)] for index, product in enumerate(self._all_products)}
        for index, product in enumerate(visible):
            pct = _fill_pct(product)
            # Animating: start from what this product showed last time (zero the first time).
            shown_qty, from_pct = self._last.get(product.barcode, (0, 0)) if animate else (product.stock_quantity, pct)
            row = _StockRow(product, tints[product.barcode], shown_qty, from_pct)
            self._rows_layout.insertWidget(index, row)
            self._rows.append(row)
        self._last = {product.barcode: (product.stock_quantity, _fill_pct(product)) for product in self._all_products}

        if animate:
            stagger_in(self._rows, step=45)
            for index, row in enumerate(self._rows):
                QTimer.singleShot(index * 45 + 120, lambda r=row: self._play_row(r))

    @staticmethod
    def _play_row(row: _StockRow) -> None:
        try:
            row.play()
        except RuntimeError:  # the list was rebuilt meanwhile and this row is gone
            pass
