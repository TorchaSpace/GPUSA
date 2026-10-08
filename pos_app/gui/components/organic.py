"""The POS app's "Organic" building blocks, painted by hand so they can move.

A Qt style sheet cannot tween a colour, grow a shadow or slide a highlight, so
everything here draws itself and eases between states with the shared
`Level` tween (shared/gui_kit/motion.py). Every motion is skipped when
`animations_enabled()` is False (tests, GPUSA_REDUCE_MOTION=1).

* `soft_shadow`   - the design's --shadow-sm/md/lg as a drop-shadow effect.
* `OrganicCard`   - a big rounded tap tile / card: colour glides on hover and
                    press, the shadow lifts, optional decorative circles swell.
* `PillButton`    - the full-width pill buttons (Card / Cash / Complete ...).
* `Chip`          - a small checkable pill (categories, filters).
* `PillNav`       - the header nav: a pill track with one highlight that SLIDES
                    from tab to tab.
* `ToastStyle`    - the dark pill used for confirmations.

Colours are passed in as hex strings; the design tokens live in pos_app/theme.py.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QPushButton, QSizePolicy, QWidget

from pos_app.theme import ORGANIC_PALETTE
from shared.gui_kit.motion import Level, animations_enabled, blend

INK = "#2e2b25"  # --color-neutral-900, the tint of every shadow

# (blur, y offset, ink alpha) for --shadow-sm / --shadow-md / --shadow-lg
_SHADOWS = {"sm": (3, 1, 36), "md": (12, 3, 41), "lg": (34, 12, 56)}


def soft_shadow(widget: QWidget, size: str = "md") -> QGraphicsDropShadowEffect:
    """Give `widget` the design's soft ink-tinted shadow and return the effect."""
    blur, offset, alpha = _SHADOWS[size]
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, offset)
    effect.setColor(QColor(46, 43, 37, alpha))
    widget.setGraphicsEffect(effect)
    return effect


class OrganicCard(QPushButton):
    """A rounded, tappable surface that can hold a layout of labels.

    Put QLabels inside with `setAttribute(Qt.WA_TransparentForMouseEvents)`
    and no background of their own. `decor` is a list of (cx, cy, r, colour)
    circles in card-relative units: cx/cy as a fraction of the width/height,
    r in pixels (the design's big offset circle on the New Sale tile)."""

    def __init__(self, bg: str, hover: str, press: str, radius: int = 40, shadow: str = "md",
                 decor: list[tuple[float, float, float, str]] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._bg, self._hover_bg, self._press_bg = bg, hover, press
        self._radius = radius
        self._decor = decor or []
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setFocusPolicy(Qt.TabFocus)
        self.setStyleSheet("QPushButton { border: none; background: transparent; text-align: left; padding: 0; }")
        self._hover = Level(self, self._on_level, 170)
        self._press = Level(self, self._on_level, 90)
        self._shadow_name = shadow
        self._effect = soft_shadow(self, shadow) if shadow else None

    # -- state -------------------------------------------------------------------
    def set_colors(self, bg: str, hover: str, press: str) -> None:
        self._bg, self._hover_bg, self._press_bg = bg, hover, press
        self.update()

    def _on_level(self, _value: float) -> None:
        if self._effect is not None:
            blur, offset, alpha = _SHADOWS[self._shadow_name]
            lift = self._hover.value * (1.0 - 0.6 * self._press.value)
            self._effect.setBlurRadius(blur + 16 * lift)
            self._effect.setOffset(0, offset + 6 * lift)
            self._effect.setColor(QColor(46, 43, 37, min(255, alpha + int(18 * lift))))
        self.update()

    def event(self, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif kind in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
            self._press.go(0.0)
        elif kind == QEvent.MouseButtonPress:
            self._press.go(1.0)
        elif kind == QEvent.MouseButtonRelease:
            self._press.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = blend(blend(self._bg, self._hover_bg, self._hover.value), self._press_bg, self._press.value)
        # Pressing sinks the card by a hair: it feels like a button, not a label.
        inset = 1.5 * self._press.value
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)
        painter.fillPath(path, QColor(colour))
        if self._decor:
            painter.setClipPath(path)
            for cx, cy, r, tone in self._decor:
                grow = r * (1.0 + 0.12 * self._hover.value)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(tone))
                painter.drawEllipse(QPointF(rect.width() * cx, rect.height() * cy), grow, grow)
        if self.hasFocus() and self.focusPolicy() != Qt.NoFocus:
            painter.setClipping(False)
            painter.setPen(QColor(ORGANIC_PALETTE["accent"]))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), self._radius - 2, self._radius - 2)


class PillButton(QPushButton):
    """A fully rounded button whose fill glides on hover and sinks on press."""

    def __init__(self, text: str, bg: str, hover: str, press: str, fg: str, height: int = 68, font_px: int = 19,
                 heading: bool = False, icon: QIcon | None = None, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._bg, self._hover_bg, self._press_bg, self._fg = bg, hover, press, fg
        self._font_px, self._heading = font_px, heading
        self._icon = icon
        self.setMinimumHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self._hover = Level(self, lambda _v: self.update(), 150)
        self._press = Level(self, lambda _v: self.update(), 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(height)

    def set_colors(self, bg: str, hover: str, press: str, fg: str) -> None:
        self._bg, self._hover_bg, self._press_bg, self._fg = bg, hover, press, fg
        self.update()

    def set_pill_icon(self, icon: QIcon | None) -> None:
        self._icon = icon
        self.update()

    def event(self, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif kind in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
            self._press.go(0.0)
        elif kind == QEvent.MouseButtonPress:
            self._press.go(1.0)
        elif kind == QEvent.MouseButtonRelease:
            self._press.go(0.0)
        elif kind == QEvent.EnabledChange:
            self.update()
        return super().event(event)

    def paintEvent(self, _event) -> None:
        p = ORGANIC_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        if enabled:
            fill = blend(blend(self._bg, self._hover_bg, self._hover.value), self._press_bg, self._press.value)
            ink = self._fg
        else:
            fill, ink = p["border"], p["text_secondary"]
        inset = 1.5 * self._press.value if enabled else 0.0
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, radius, radius)
        font = QFont(self.font())
        font.setPixelSize(self._font_px)
        font.setWeight(QFont.Normal if self._heading else QFont.Bold)
        if self._heading:
            font.setFamily(ORGANIC_PALETTE["font_heading"].split(",")[0].strip("'\" "))
        painter.setFont(font)
        painter.setPen(QColor(ink))
        metrics = QFontMetrics(font)
        text_w = metrics.horizontalAdvance(self.text())
        icon_w = (self._icon.actualSize(QSize(24, 24)).width() + 8) if self._icon is not None and not self._icon.isNull() else 0
        left = rect.left() + (rect.width() - text_w - icon_w) / 2
        if icon_w:
            pixmap = self._icon.pixmap(QSize(24, 24), QIcon.Normal if enabled else QIcon.Disabled)
            painter.drawPixmap(int(left), int(rect.center().y() - 12), pixmap)
            left += icon_w
        painter.drawText(QRectF(left, rect.top(), text_w + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class Chip(QPushButton):
    """A small checkable pill: idle = `idle_bg`, on = `on_bg`. The fill and the
    text colour glide when it is picked."""

    def __init__(self, text: str, idle_bg: str, on_bg: str, idle_fg: str, on_fg: str, height: int = 44, font_px: int = 15,
                 parent: QWidget | None = None):
        super().__init__(text, parent)
        self._idle_bg, self._on_bg, self._idle_fg, self._on_fg = idle_bg, on_bg, idle_fg, on_fg
        self._font_px = font_px
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setFixedHeight(height)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self._on = Level(self, lambda _v: self.update(), 200, 1.0 if self.isChecked() else 0.0)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self.toggled.connect(lambda on: self._on.go(1.0 if on else 0.0))
        font = QFont(self.font())
        font.setPixelSize(font_px)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self.setMinimumWidth(QFontMetrics(font).horizontalAdvance(text) + 40)

    def setChecked(self, on: bool) -> None:
        super().setChecked(on)
        self._on.go(1.0 if on else 0.0, animate=False)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Enter, QEvent.HoverEnter):
            self._hover.go(1.0)
        elif event.type() in (QEvent.Leave, QEvent.HoverLeave):
            self._hover.go(0.0)
        return super().event(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        idle = blend(self._idle_bg, ORGANIC_PALETTE["border"], 0.55 * self._hover.value)
        fill = blend(idle, self._on_bg, self._on.value)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        painter.setFont(self.font())
        painter.setPen(QColor(blend(self._idle_fg, self._on_fg, self._on.value)))
        painter.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text())


class PillNav(QWidget):
    """The header navigation: a surface-coloured pill track with ONE raised
    highlight that slides to whichever tab is picked."""

    page_selected = Signal(str)

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self._keys = [key for key, _text in items]
        self._labels = [text for _key, text in items]
        self._active = 0
        self._hover_index = -1
        self._hover = [Level(self, lambda _v: self.update(), 140) for _ in items]
        self._font = QFont(self.font())
        self._font.setPixelSize(16)
        self._font.setWeight(QFont.DemiBold)
        self._metrics = QFontMetrics(self._font)
        self._pad = 22
        self._track = 6
        self._gap = 6
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(300)
        self._slide.setEasingCurve(QEasingCurve.OutBack)
        self._slide.valueChanged.connect(self._on_slide)
        self._x, self._w = 0.0, 0.0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedWidth(self._total_width())
        self._x, self._w = self._cell(0)
        self._palette = p

    # -- geometry ----------------------------------------------------------------
    def _cell_width(self, index: int) -> float:
        return self._metrics.horizontalAdvance(self._labels[index]) + 2 * self._pad

    def _total_width(self) -> int:
        cells = sum(self._cell_width(i) for i in range(len(self._labels)))
        return int(cells + self._gap * (len(self._labels) - 1) + 2 * self._track)

    def _cell(self, index: int) -> tuple[float, float]:
        x = self._track + sum(self._cell_width(i) + self._gap for i in range(index))
        return x, self._cell_width(index)

    def sizeHint(self) -> QSize:
        return QSize(self._total_width(), 60)

    # -- state -------------------------------------------------------------------
    def active_key(self) -> str:
        return self._keys[self._active]

    def set_active(self, key: str, animate: bool = True) -> None:
        if key not in self._keys:
            return
        index = self._keys.index(key)
        if index == self._active and abs(self._x - self._cell(index)[0]) < 0.5:
            return
        self._active = index
        x, w = self._cell(index)
        if not animate or not animations_enabled():
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

    def _index_at(self, x: float) -> int:
        for i in range(len(self._labels)):
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
            self.set_active(self._keys[index])
            self.page_selected.emit(self._keys[index])
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p["surface"]))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        cell_h = track.height() - 2 * self._track
        # the raised highlight (soft shadow first, then the pill)
        pill = QRectF(self._x, self._track, self._w, cell_h)
        painter.setBrush(QColor(46, 43, 37, 30))
        painter.drawRoundedRect(pill.translated(0, 1.5), cell_h / 2, cell_h / 2)
        painter.setBrush(QColor(p["surface_raised"]))
        painter.drawRoundedRect(pill, cell_h / 2, cell_h / 2)
        painter.setFont(self._font)
        for i, text in enumerate(self._labels):
            x, w = self._cell(i)
            # how much of this label sits under the highlight decides its colour
            overlap = max(0.0, min(x + w, self._x + self._w) - max(x, self._x)) / w
            idle = blend("#474238", p["text_primary"], self._hover[i].value * 0.6)
            painter.setPen(QColor(blend(idle, p["text_primary"], min(1.0, overlap * 1.4))))
            painter.drawText(QRectF(x, self._track, w, cell_h), Qt.AlignCenter, text)


def toast_style() -> str:
    """The design's confirmation: a dark pill, cream text."""
    p = ORGANIC_PALETTE
    return (
        f"#motionToast {{ background-color: {p['text_primary']}; color: {p['background']}; border: none; "
        f"border-radius: 24px; padding: 14px 26px; font-size: 16px; font-weight: 600; }}"
    )
