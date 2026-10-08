"""Home screen - recreates the mockup's Dashboard section: a greeting
plus 3 large tap tiles (New Sale / Receive Inventory / My Local Stock).

Real vs. placeholder, explicitly:
- "X out"/"Y low" badge on the My Local Stock tile: REAL, computed from
  database.stock_repository - this dealership's shelf (same stock_status() classification the
  My Local Stock screen itself uses - see task #59).
- "N arriving" badge on the Receive Inventory tile: REAL - shipments
  still on their way to this terminal's dealership
  (shipment_repository.count_incoming(); every dealership's when the
  terminal has no setup file).
- The mockup's per-minute greeting ("Good afternoon, Selin") and date
  line are real (wall-clock), not mock data - there's no reason to fake
  the clock.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from database import shipment_repository, stock_repository
from database.exceptions import DataAccessError
from pos_app.gui import icons
from pos_app.gui.components.organic import OrganicCard
from pos_app.gui.product_status import stock_status
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.gui_kit.motion import animations_enabled, count_up
from shared.i18n import tr
from shared.models import UNASSIGNED, StockLocation

_CIRCLE = 88  # the mockup's icon disc
_POP = 12  # room around the disc so it can swell on hover without clipping
_ICON_PX = 40
_PAD = 36  # tile padding
_CELL_SIDE, _CELL_TOP, _CELL_BOTTOM = 10, 12, 24  # room around a tile for its soft shadow


class _IconDisc(QWidget):
    """The 88px round icon badge. It sits in a slightly larger transparent
    box so it can swell a little while the tile is hovered."""

    def __init__(self, tile: "_Tile", fill: str, path: str, ink: str):
        super().__init__(tile)
        self._tile = tile
        self._fill = QColor(fill)
        self._icon = icons.pixmap(path, ink, _ICON_PX)
        side = _CIRCLE + 2 * _POP
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:
        scale = 1.0 + 0.07 * self._tile.hover_level()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(scale, scale)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._fill)
        painter.drawEllipse(QRectF(-_CIRCLE / 2, -_CIRCLE / 2, _CIRCLE, _CIRCLE))
        half = _ICON_PX / 2
        painter.drawPixmap(QRectF(-half, -half, _ICON_PX, _ICON_PX), self._icon, QRectF(self._icon.rect()))


class _Tile(OrganicCard):
    """An OrganicCard whose icon disc follows the hover tween, and whose
    decorative circle can be placed in pixels from the top-right corner."""

    def __init__(self, bg: str, hover: str, press: str, decor_tone: str | None = None):
        super().__init__(bg, hover, press, radius=40, shadow="md",
                         decor=[(1.0, 0.0, 130.0, decor_tone)] if decor_tone else None)
        self._decor_tone = decor_tone
        self._disc: _IconDisc | None = None
        self.setMinimumHeight(260)

    def hover_level(self) -> float:
        return self._hover.value

    def set_disc(self, disc: _IconDisc) -> None:
        self._disc = disc

    def _on_level(self, value: float) -> None:
        super()._on_level(value)
        if self._disc is not None:
            self._disc.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._decor_tone and self.width() > 0 and self.height() > 0:
            # mockup: 260px circle at right:-70px; top:-70px => centre (w-60, 60)
            self._decor = [((self.width() - 60) / self.width(), 60 / self.height(), 130.0, self._decor_tone)]


def _label(text: str, css: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setStyleSheet(f"QLabel#{name} {{ background: transparent; border: none; {css} }}")
    label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    label.setWordWrap(wrap)
    return label


def _badge(name: str, bg: str, fg: str) -> QLabel:
    label = _label("", f"background-color: {bg}; color: {fg}; border-radius: 18px; font-weight: 700; "
                       "font-size: 15px; padding: 8px 14px;", name)
    label.setAlignment(Qt.AlignCenter)
    return label


class HomePage(QWidget):
    tile_clicked = Signal(str)  # "sale" | "receive" | "stock"

    def __init__(self, cashier_first_name: str, dealership_code: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = dealership_code
        self._location = StockLocation.dealership(dealership_code) if dealership_code else UNASSIGNED
        self._intro: list[tuple[QWidget, QParallelAnimationGroup | None, QPoint | None]] = []
        self._intro_done_once = False
        p = ORGANIC_PALETTE
        self.setObjectName("homePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#homePage {{ background-color: {p['background']}; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28 - _CELL_SIDE, 28, 28 - _CELL_SIDE, 32 - _CELL_BOTTOM)
        outer.setSpacing(28 - _CELL_TOP)

        self._greeting_block = QWidget()
        greeting_layout = QVBoxLayout(self._greeting_block)
        greeting_layout.setContentsMargins(_CELL_SIDE, 0, _CELL_SIDE, 0)
        greeting_layout.setSpacing(4)
        self._date_label = _label("", f"font-size: 16px; color: {p['text_secondary']};", "homeDate")
        greeting_layout.addWidget(self._date_label)
        self._greeting_label = _label(
            "", f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 48px; color: {p['text_primary']};",
            "homeGreeting")
        greeting_layout.addWidget(self._greeting_label)
        outer.addWidget(self._greeting_block)

        self._set_greeting(cashier_first_name)

        tiles = QGridLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(0)
        tiles.setColumnStretch(0, 5)
        tiles.setColumnStretch(1, 4)
        tiles.setColumnStretch(2, 4)

        self._sale_tile = self._build_sale_tile()
        self._receive_tile = self._build_receive_tile()
        self._stock_tile = self._build_stock_tile()
        self._cells = [self._cell(t) for t in (self._sale_tile, self._receive_tile, self._stock_tile)]
        for column, cell in enumerate(self._cells):
            tiles.addWidget(cell, 0, column)

        outer.addLayout(tiles, stretch=1)

        self.reload_badges()

    # -- construction ---------------------------------------------------------------
    @staticmethod
    def _cell(tile: _Tile) -> QWidget:
        """A transparent holder: it carries the intro fade/slide while the tile
        keeps its own shadow, and leaves the shadow room to draw."""
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(_CELL_SIDE, _CELL_TOP, _CELL_SIDE, _CELL_BOTTOM)
        layout.addWidget(tile)
        return cell

    def _tile_layout(self, tile: _Tile, fill: str, path: str, ink: str, badges: list[QLabel] | None):
        """Padding 36, icon disc top-left, optional badges top-right, text block
        at the bottom. Returns the layout so the caller adds the text."""
        layout = QVBoxLayout(tile)
        pad_top = _PAD - _POP  # the disc's transparent margin counts toward the padding
        layout.setContentsMargins(_PAD - _POP, pad_top, _PAD, _PAD)
        layout.setSpacing(0)
        disc = _IconDisc(tile, fill, path, ink)
        tile.set_disc(disc)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(disc, alignment=Qt.AlignTop | Qt.AlignLeft)
        row.addStretch(1)
        if badges:
            column = QVBoxLayout()
            column.setContentsMargins(0, _POP, 0, 0)  # flex-start with the disc itself
            column.setSpacing(8)
            for badge in badges:
                column.addWidget(badge, alignment=Qt.AlignRight)
            column.addStretch(1)
            row.addLayout(column)
        layout.addLayout(row)
        layout.addStretch(1)
        return layout

    @staticmethod
    def _add_text(layout: QVBoxLayout, tile: _Tile, key: str, title_px: int, sub_px: int,
                  title_colour: str, sub_colour: str) -> None:
        text_row = QVBoxLayout()
        text_row.setContentsMargins(_POP, 0, 0, 0)
        text_row.setSpacing(12)
        text_row.addWidget(_label(tr(f"pos.home.{key}_title"),
                                  f"font-family: {FONT_HEADING_CSS}; font-size: {title_px}px; color: {title_colour};",
                                  f"{key}Title"))
        text_row.addWidget(_label(tr(f"pos.home.{key}_subtitle"), f"font-size: {sub_px}px; color: {sub_colour};",
                                  f"{key}Subtitle", wrap=True))
        layout.addLayout(text_row)

    def _build_sale_tile(self) -> _Tile:
        p = ORGANIC_PALETTE
        tile = _Tile(p["accent"], "#b2622d", "#8c491a", decor_tone="#d67f48")
        tile.setObjectName("saleTile")
        tile.clicked.connect(lambda: self.tile_clicked.emit("sale"))
        layout = self._tile_layout(tile, "#ffffff", icons.CART, "#8c491a", None)
        self._add_text(layout, tile, "sale", 52, 18, "#ffffff", "#fff2eb")
        return tile

    def _build_receive_tile(self) -> _Tile:
        p = ORGANIC_PALETTE
        tile = _Tile(p["accent_2"], "#728157", "#56633f")
        tile.setObjectName("receiveTile")
        tile.clicked.connect(lambda: self.tile_clicked.emit("receive"))
        self._receive_badge = _badge("receiveBadge", "#ffffff", "#3d472b")
        layout = self._tile_layout(tile, "#ffffff", icons.TRUCK, "#56633f", [self._receive_badge])
        self._add_text(layout, tile, "receive", 40, 17, "#ffffff", "#f0fae1")
        return tile

    def _build_stock_tile(self) -> _Tile:
        p = ORGANIC_PALETTE
        tile = _Tile(p["surface_raised"], "#eee7db", "#dcd3c4")
        tile.setObjectName("stockTile")
        tile.clicked.connect(lambda: self.tile_clicked.emit("stock"))
        self._out_badge = _badge("outBadge", "#d8412f", "#ffffff")
        self._low_badge = _badge("lowBadge", "#f2c230", "#3a2a05")
        layout = self._tile_layout(tile, p["surface"], icons.PACKAGE, p["text_primary"],
                                   [self._out_badge, self._low_badge])
        self._add_text(layout, tile, "stock", 40, 17, p["text_primary"], p["text_secondary"])
        return tile

    # -- greeting ---------------------------------------------------------------------
    def set_cashier(self, cashier_first_name: str) -> None:
        """A new cashier signed in (Switch cashier)."""
        self._set_greeting(cashier_first_name)

    def _set_greeting(self, cashier_first_name: str) -> None:
        now = datetime.now()
        self._date_label.setText(tr("pos.home.date").format(
            weekday=tr(f"format.weekday_long.{now.weekday()}"),
            day=now.strftime("%d"),
            month=tr(f"pos.home.month_long.{now.month}"),
        ))
        hour = now.hour
        part_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self._greeting_label.setText(tr(f"pos.home.greeting_{part_of_day}").format(name=cashier_first_name))

    # -- motion ------------------------------------------------------------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._play_intro()

    def hideEvent(self, event) -> None:
        self._finish_intro()
        super().hideEvent(event)

    def _finish_intro(self) -> None:
        """Snap any running intro to its end state (also before a replay)."""
        pending, self._intro = self._intro, []
        for widget, group, target in pending:
            try:
                if group is not None:
                    group.stop()
                widget.setGraphicsEffect(None)
                if target is not None:
                    widget.move(target)
            except RuntimeError:
                pass  # the widget was destroyed with the page

    def _play_intro(self, step: int = 70, duration: int = 320, rise: int = 22) -> None:
        """Greeting, then the three tiles, each fades up in turn. The widgets
        are hidden at once (opacity 0) so nothing flashes before its turn."""
        self._finish_intro()
        if not animations_enabled():
            return
        for index, widget in enumerate([self._greeting_block, *self._cells]):
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            self._intro.append((widget, None, None))
            QTimer.singleShot(index * step, lambda w=widget, e=effect: self._rise(w, e, duration, rise))

    def _rise(self, widget: QWidget, effect: QGraphicsOpacityEffect, duration: int, rise: int) -> None:
        entry = next((i for i, item in enumerate(self._intro) if item[0] is widget), None)
        if entry is None or widget.graphicsEffect() is not effect:
            return  # the intro was cancelled or replayed in the meantime
        end = widget.pos()
        fade = QPropertyAnimation(effect, b"opacity", widget)
        fade.setDuration(duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        slide = QPropertyAnimation(widget, b"pos", widget)
        slide.setDuration(duration)
        slide.setStartValue(QPoint(end.x(), end.y() + rise))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutCubic)
        group = QParallelAnimationGroup(widget)
        group.addAnimation(fade)
        group.addAnimation(slide)

        def done() -> None:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            self._intro = [item for item in self._intro if item[0] is not widget]

        group.finished.connect(done)
        self._intro[entry] = (widget, group, end)
        group.start()

    # -- data ---------------------------------------------------------------------------
    def reload_badges(self) -> None:
        try:
            products = [p for p in stock_repository.products_at(self._location)
                        if p.stocked_here or p.stock_quantity > 0]
        except DataAccessError:
            products = []
        out_count = sum(1 for product in products if stock_status(product) == "out")
        low_count = sum(1 for product in products if stock_status(product) == "low")
        count_up(self._out_badge, tr("pos.home.badge_out").format(n=out_count))
        count_up(self._low_badge, tr("pos.home.badge_low").format(n=low_count))
        try:
            arriving = shipment_repository.count_incoming(self._dealership_code)
        except DataAccessError:
            arriving = 0
        count_up(self._receive_badge, tr("pos.home.badge_arriving").format(n=arriving))
