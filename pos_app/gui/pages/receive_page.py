"""Receive Inventory - the real build of the POS mockup's "Receive
Inventory" screen, replacing its themed placeholder.

Recreated from the mockup:
- Left: "Incoming" - one card per shipment coming to this dealership
  (number, status tag, where it's from, ETA · N lines). The picked card is
  raised (neutral-100 + shadow), the others sit flat on the surface colour;
  the change glides.
- Right: the selected shipment - "<origin> · Driver <name>", "Shipment
  SH-…", the Accept All / Report Discrepancy pill, one row per product
  (round check toggle, product, Expected, Received with −/+ in report mode),
  and the footer "N of M checked · K discrepancies" with Complete
  receipt / Send report & receive.

Motion (all skipped when `animations_enabled()` is False): the check
toggle's fill grows from its centre and the tick draws itself; a row's
background glides between plain / checked / discrepancy; the Accept All and
Report Discrepancy fills fade; the footer count counts up; switching
shipment fades the header and lets the rows rise in one after another.
Rows and cards are kept between renders (and updated in place) so those
transitions have something to animate from - the shipment poll never makes
the panel flicker.

Real: database.shipment_repository (created and dispatched by the depot's
Console > Shipments). Completing a receipt marks the shipment delivered,
records what arrived line by line, and puts the received units on THIS
dealership's shelf (where the count differs from what was shipped, the
difference is written off / added - see shipment_repository's
docstring); the depot and admin_app see the report straight away.

Which shipments: the ones addressed to THIS terminal's dealership (its
setup file's code - shared.dealership_bootstrap.load_dealership_identity).
A terminal with no setup file (e.g. a `python -m pos_app.main` dev run)
lists every incoming shipment and says so, rather than showing nothing.

Deliberately different from the mockup: the tags are the real, derived
status ("Arriving" = due within the hour, "Delayed", "On the way",
"Scheduled", "Received") instead of the mockup's "At door" - nothing
tells this system a truck is physically at the door. And reporting a
discrepancy offers an optional note (what happened), which the depot
sees on the shipment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import hypot

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from database import shipment_repository
from database.exceptions import DATABASE_ERRORS
from pos_app.gui import icons
from pos_app.gui.components.organic import OrganicCard, PillButton, soft_shadow, toast_style
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.constants import SHIPMENT_POLL_INTERVAL_MS
from shared.distribution import eta_text, live_status
from shared.formatting import parse_db_timestamp
from shared.gui_kit.motion import Level, animations_enabled, blend, count_up, fade_in, stagger_in, toast
from shared.gui_kit.polling import PollingTimer
from shared.i18n import plural, tr
from shared.models import Shipment
from shared import current_session
from shared.textcase import upper
from shared.warehousing import tr_or

_YELLOW = "#f2c230"
_RED_TEXT = "#9a2a1d"
_AMBER_TEXT = "#8a5a00"  # the mockup's "received differs" number colour
_ROW_CHECKED = "#f0fae1"  # --color-accent-2-100
_ROW_ISSUE = "#fdf0c4"
_NEUTRAL_200 = "#eee7db"
_NEUTRAL_400 = "#b9b0a1"
_RECENT = timedelta(hours=24)  # keep a received shipment on the list this long

_TAGS = {  # live status -> (tag text KEY - tr() at render time, background, text colour)
    "Arriving": ("pos.receive.tag.arriving", "#f3d9c6", "#7a3d15"),
    "Delayed": ("pos.receive.tag.delayed", _YELLOW, "#3a2a05"),
    "In Transit": ("pos.receive.tag.in_transit", "#e3dccb", "#4a4336"),
    "Scheduled": ("pos.receive.tag.scheduled", "#e3dccb", "#4a4336"),
    "Delivered": ("pos.receive.tag.delivered", "#e1eecc", "#3d472b"),
}

_CLEAR = "background: transparent; border: none;"


def _label(text: str, css: str, *, passthrough: bool = False) -> QLabel:
    """A QLabel that never paints a box behind its text (the app-wide base
    stylesheet would), optionally ignoring the mouse so a card/row under it
    gets the click."""
    label = QLabel(text)
    label.setStyleSheet(f"{_CLEAR} {css}")
    if passthrough:
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
    return label


def _scroll_area(host: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    scroll.viewport().setObjectName("clear")
    scroll.setWidget(host)
    return scroll


def _discard(widget: QWidget) -> None:
    widget.hide()
    widget.deleteLater()


# --- widgets ------------------------------------------------------------------------------


class _ShipmentCard(OrganicCard):
    """One "Incoming" card. Selecting it raises it (neutral-100 + shadow md),
    deselecting lets it sink back to the flat surface colour - both glide."""

    _SEL = ("#f9f4ed", "#fffaf2", "#efe7d9")  # neutral-100 and its hover / press
    _IDLE = ("#ebddc5", "#e6d6ba", "#decdb0")  # --color-surface and its hover / press

    def __init__(self, shipment: Shipment, selected: bool, parent=None):
        super().__init__(*(self._SEL if selected else self._IDLE), radius=28, shadow="md", parent=parent)
        p = ORGANIC_PALETTE
        self.setObjectName("shipCard")
        self.setFixedHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._sel = Level(self, self._on_select, 240, 1.0 if selected else 0.0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._number = _label("", f"font-weight: 700; font-size: 17px; color: {p['text_primary']};", passthrough=True)
        top.addWidget(self._number)
        top.addStretch(1)
        self.tag = _label("", "", passthrough=True)
        top.addWidget(self.tag)
        layout.addLayout(top)
        self._origin = _label("", f"font-size: 15px; color: {p['text_secondary']};", passthrough=True)
        self._eta = _label("", f"font-size: 15px; color: {p['text_secondary']};", passthrough=True)
        layout.addWidget(self._origin)
        layout.addWidget(self._eta)
        layout.addStretch(1)
        self.update_shipment(shipment)
        self._on_select(self._sel.value)

    def update_shipment(self, shipment: Shipment) -> None:
        text_key, bg, fg = _TAGS.get(live_status(shipment), _TAGS["Scheduled"])
        self._number.setText(shipment.number)
        self.tag.setText(tr(text_key))
        self.tag.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border: none; border-radius: 12px; "
            f"padding: 4px 10px; font-size: 13px; font-weight: 700;"
        )
        self._origin.setText(shipment.origin)
        self._eta.setText(tr("pos.receive.card_lines").format(eta=eta_text(shipment), n=len(shipment.lines)))

    def set_selected(self, selected: bool, animate: bool = True) -> None:
        self._sel.go(1.0 if selected else 0.0, animate)

    def _on_select(self, value: float) -> None:
        a, b = self._IDLE, self._SEL
        self.set_colors(blend(a[0], b[0], value), blend(a[1], b[1], value), blend(a[2], b[2], value))
        self._on_level(0.0)

    def _on_level(self, _value: float) -> None:
        # Same lift-on-hover as OrganicCard, but the resting shadow fades out with the selection.
        sel = getattr(self, "_sel", None)
        s = sel.value if sel is not None else 1.0
        if self._effect is not None:
            lift = self._hover.value * (1.0 - 0.6 * self._press.value)
            self._effect.setBlurRadius(4 + 8 * s + 16 * lift)
            self._effect.setOffset(0, 1 + 2 * s + 6 * lift)
            self._effect.setColor(QColor(46, 43, 37, min(255, int(41 * s + 18 * lift))))
        self.update()


class _CheckToggle(QAbstractButton):
    """The 48px round check: a 3px neutral ring when idle; checking grows the
    sage fill from the centre while the tick draws itself."""

    _TICK = (QPointF(4, 12), QPointF(9, 17), QPointF(20, 6))  # the mockup's check path (24px grid)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clear")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setFixedSize(48, 48)
        self._checked = False
        self._on = Level(self, lambda _v: self.update(), 300)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self._press = Level(self, lambda _v: self.update(), 80)

    def is_on(self) -> bool:
        return self._checked

    def set_on(self, on: bool, animate: bool = True) -> None:
        self._checked = on
        self._on.go(1.0 if on else 0.0, animate)

    def sizeHint(self) -> QSize:
        return QSize(48, 48)

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

    @classmethod
    def _tick_path(cls, fraction: float) -> QPainterPath:
        points = [QPointF(pt.x() + 12, pt.y() + 12) for pt in cls._TICK]  # centred in the 48px circle
        lengths = [hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(points, points[1:])]
        remaining = fraction * sum(lengths)
        path = QPainterPath(points[0])
        for (a, b), length in zip(zip(points, points[1:]), lengths):
            if remaining <= 0:
                break
            if remaining >= length:
                path.lineTo(b)
                remaining -= length
            else:
                path.lineTo(a + (b - a) * (remaining / length))
                remaining = 0
        return path

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        on = self._on.value
        hover = self._hover.value
        shrink = 1.5 * self._press.value
        if on < 0.99:  # the idle ring fades as the fill takes over
            painter.setOpacity(1.0 - on)
            pen = QPen(QColor(blend(_NEUTRAL_400, "#8f8677", hover)), 3)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(centre, 22.5 - shrink, 22.5 - shrink)
            painter.setOpacity(1.0)
        if on > 0.01:
            radius = (24.0 - shrink) * on
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(blend(ORGANIC_PALETTE["accent_2"], "#6a7a4f", hover)))
            painter.drawEllipse(centre, radius, radius)
        fraction = max(0.0, (on - 0.3) / 0.7)
        if fraction > 0.0:
            pen = QPen(QColor("white"), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(self._tick_path(fraction))
        if self.hasFocus():
            painter.setPen(QPen(QColor(ORGANIC_PALETTE["accent"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(centre, 23.0, 23.0)


class _StepButton(QPushButton):
    """The 44px round −/+ stepper: neutral-100 disc with shadow sm."""

    def __init__(self, glyph: str, parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("clear")
        self.setFixedSize(44, 44)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self._hover = Level(self, lambda _v: self.update(), 140)
        self._press = Level(self, lambda _v: self.update(), 80)
        soft_shadow(self, "sm")

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
        p = ORGANIC_PALETTE
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        fill = blend(blend(p["surface_raised"], _NEUTRAL_200, self._hover.value), p["border"], self._press.value)
        inset = 1.0 * self._press.value
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawEllipse(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset))
        font = QFont(self.font())
        font.setPixelSize(20)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(p["text_primary"]))
        painter.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text())


class _SegmentTrack(QWidget):
    """The surface-coloured pill the Accept All / Report Discrepancy buttons sit in."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("clear")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ORGANIC_PALETTE["surface"]))
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)


class _SegmentButton(QPushButton):
    """A 56px pill with an icon + label. Its fill fades in when it becomes the
    active choice and the text/icon colour crossfades with it."""

    def __init__(self, text: str, icon_path: str, active_bg: str, active_fg: str, idle_fg: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("clear")
        self._active_bg, self._active_fg, self._idle_fg = active_bg, active_fg, idle_fg
        self._icon_idle = icons.pixmap(icon_path, idle_fg, 20)
        self._icon_active = icons.pixmap(icon_path, active_fg, 20)
        self._font = QFont(self.font())
        self._font.setPixelSize(17)
        self._font.setWeight(QFont.Bold)
        self.setFont(self._font)
        self._text_w = QFontMetrics(self._font).horizontalAdvance(text)
        self.setFixedSize(2 * 24 + 20 + 8 + self._text_w + 2, 56)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self._active = Level(self, lambda _v: self.update(), 220)
        self._hover = Level(self, lambda _v: self.update(), 140)
        self._press = Level(self, lambda _v: self.update(), 80)

    def set_active(self, active: bool, animate: bool = True) -> None:
        self._active.go(1.0 if active else 0.0, animate)

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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        base = 1.0 if self.isEnabled() else 0.45
        t = self._active.value
        inset = 1.5 * self._press.value
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        if self._hover.value > 0.01 and t < 1.0 and self.isEnabled():
            painter.setBrush(QColor(46, 43, 37, int(18 * self._hover.value * (1.0 - t))))
            painter.drawRoundedRect(rect, radius, radius)
        if t > 0.01:
            painter.setOpacity(base * t)
            painter.setBrush(QColor(self._active_bg))
            painter.drawRoundedRect(rect, radius, radius)
        painter.setOpacity(base)
        left = rect.left() + (rect.width() - 20 - 8 - self._text_w) / 2
        top = int(rect.center().y() - 10)
        painter.setOpacity(base * (1.0 - t))
        painter.drawPixmap(int(left), top, self._icon_idle)
        painter.setOpacity(base * t)
        painter.drawPixmap(int(left), top, self._icon_active)
        painter.setOpacity(base)
        painter.setFont(self._font)
        painter.setPen(QColor(blend(self._idle_fg, self._active_fg, t)))
        painter.drawText(
            QRectF(left + 28, rect.top(), self._text_w + 2, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text()
        )


class _CompleteButton(PillButton):
    """The dark "Complete receipt" pill. Disabled it is the same pill at 40%
    opacity (the mockup), not a grey one. Paints "&&" as "&" itself, because
    it draws its own text instead of going through Qt's mnemonic handling."""

    def __init__(self, parent=None):
        p = ORGANIC_PALETTE
        super().__init__("", p["text_primary"], "#474238", "#2e2b25", p["background"], height=60, font_px=18, parent=parent)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

    def _shown_text(self) -> str:
        return self.text().replace("&&", "&")

    def sizeHint(self) -> QSize:
        font = QFont(self.font())
        font.setPixelSize(self._font_px)
        font.setWeight(QFont.Bold)
        return QSize(QFontMetrics(font).horizontalAdvance(self._shown_text()) + 64, 60)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        fill = blend(blend(self._bg, self._hover_bg, self._hover.value), self._press_bg, self._press.value) if enabled else self._bg
        painter.setOpacity(1.0 if enabled else 0.4)
        inset = 1.5 * self._press.value if enabled else 0.0
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        font = QFont(self.font())
        font.setPixelSize(self._font_px)
        font.setWeight(QFont.Bold)
        painter.setFont(font)
        painter.setPen(QColor(self._fg))
        painter.drawText(rect, Qt.AlignCenter, self._shown_text())


class _CountLabel(QLabel):
    """A rich-text label with ONE number in it that `count_up` can drive.

    count_up only animates a label whose whole text is a number, so this label
    reports just that number from text() and wraps it in a fixed template
    (the translated "<b>N of M</b> checked" etc.) whenever it is set."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pre = self._post = ""
        self._number = "0"
        self.setTextFormat(Qt.RichText)

    def configure(self, pre: str, post: str) -> None:
        self._pre, self._post = pre, post
        super().setText(pre + self._number + post)

    def text(self) -> str:  # what count_up reads back as "the number shown now"
        return self._number

    def setText(self, text: str) -> None:
        self._number = text
        super().setText(self._pre + text + self._post)

    def full_text(self) -> str:
        return self._pre + self._number + self._post


class _Panel(QWidget):
    """The right-hand card: radius 36, neutral-100, shadow sm."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("receiveDetail")
        soft_shadow(self, "sm")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ORGANIC_PALETTE["surface_raised"]))
        painter.drawRoundedRect(QRectF(self.rect()), 36, 36)


class _Footer(QWidget):
    """neutral-200 bar along the bottom of the panel: square top, panel-round bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("receiveFooter")

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_NEUTRAL_200))
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, 36, 36)
        painter.drawRect(QRectF(0, 0, rect.width(), rect.height() - 36))


class _LineRow(QWidget):
    """One product line. Persistent: `refresh()` moves it to a new state and the
    background, check toggle and number animate from where they were."""

    def __init__(self, line, on_toggle, on_step, parent=None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.setObjectName("receiveRow")
        self._plain = p["surface_raised"]  # a "transparent" row shows the panel
        self._from = self._to = self._plain
        self._glide = Level(self, lambda _v: self.update(), 260)
        self._value_colour = ""
        barcode = line.product_barcode

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        holder = QWidget()
        holder.setObjectName("clear")
        holder.setFixedWidth(64)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.check = _CheckToggle()
        self.check.clicked.connect(lambda _c=False: on_toggle(barcode))
        holder_layout.addWidget(self.check, 0, Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(holder)

        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_label(line.product_name, f"font-weight: 700; font-size: 17px; color: {p['text_primary']};"))
        names.addWidget(_label(line.product_barcode, f"font-size: 14px; color: {p['text_secondary']};"))
        layout.addLayout(names, stretch=1)

        expected = _label(
            str(line.expected_qty), f"font-size: 20px; font-weight: 700; color: {p['text_primary']};"
        )
        expected.setFixedWidth(110)
        expected.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(expected)

        received_box = QHBoxLayout()
        received_box.setContentsMargins(0, 0, 0, 0)
        received_box.setSpacing(6)
        received_box.addStretch(1)
        self.minus = _StepButton("−")
        self.minus.clicked.connect(lambda _c=False: on_step(barcode, -1))
        self.plus = _StepButton("+")
        self.plus.clicked.connect(lambda _c=False: on_step(barcode, 1))
        self.value = QLabel(str(line.expected_qty))
        self.value.setMinimumWidth(44)
        self.value.setAlignment(Qt.AlignCenter)
        received_box.addWidget(self.minus)
        received_box.addWidget(self.value)
        received_box.addWidget(self.plus)
        received_widget = QWidget()
        received_widget.setObjectName("clear")
        received_widget.setFixedWidth(190)
        received_widget.setLayout(received_box)
        layout.addWidget(received_widget)
        self._style_value(p["text_primary"])

    def _style_value(self, colour: str) -> None:
        if colour != self._value_colour:
            self._value_colour = colour
            self.value.setStyleSheet(f"{_CLEAR} font-size: 20px; font-weight: 700; color: {colour};")

    def set_background(self, colour: str, animate: bool) -> None:
        if colour == self._to:
            return
        if not animate or not animations_enabled():
            self._from = self._to = colour
            self._glide.go(0.0, animate=False)
            return
        self._from = blend(self._from, self._to, self._glide.value)  # wherever the glide had got to
        self._to = colour
        self._glide.go(0.0, animate=False)
        self._glide.go(1.0)

    def refresh(self, checked: bool, received: int, expected: int, report: bool, animate: bool) -> None:
        differs = received != expected
        self.set_background(_ROW_ISSUE if differs else (_ROW_CHECKED if checked else self._plain), animate)
        if checked != self.check.is_on():
            self.check.set_on(checked, animate)
        self._style_value(_AMBER_TEXT if differs else ORGANIC_PALETTE["text_primary"])
        if animate:
            count_up(self.value, str(received), 200)
        elif self.value.text() != str(received):
            self.value.setText(str(received))
        self.minus.setVisible(report)
        self.plus.setVisible(report)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(blend(self._from, self._to, self._glide.value)))
        painter.drawRoundedRect(QRectF(self.rect()), 24, 24)


# --- the page -----------------------------------------------------------------------------


class ReceivePage(QWidget):
    """Emits `stock_changed` after a receipt that moved stock (a
    discrepancy), so the host can refresh stock views and badges."""

    stock_changed = Signal()
    incoming_changed = Signal()

    def __init__(self, dealership_code: str | None, dealership_name: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self._code = dealership_code
        self._dealer_name = dealership_name
        self._shipments: list[Shipment] = []
        self._selected_id: int | None = None
        self._checked: dict[int, set[str]] = {}
        self._received: dict[int, dict[str, int]] = {}
        self._report_mode: dict[int, bool] = {}
        self._card_holders: dict[int, QWidget] = {}
        self._cards: dict[int, _ShipmentCard] = {}
        self._rows: dict[str, _LineRow] = {}
        self._rows_key: tuple | None = None
        self._intro_played = False
        self.setObjectName("receivePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Containers named "clear" let the panel behind them show through
        # (the app-wide base stylesheet otherwise paints every widget).
        self.setStyleSheet(
            f"#receivePage {{ background-color: {p['background']}; }} "
            f"QWidget#clear, QWidget#receiveRow, QWidget#receiveFooter, QWidget#receiveDetail "
            f"{{ background: transparent; }}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(22, 12, 28, 28)  # 22 + the cards' 6px shadow room = the design's 28
        outer.setSpacing(18)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        heading = _label(
            tr("pos.receive.incoming"),
            f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 34px; color: {p['text_primary']};",
        )
        heading.setContentsMargins(6, 0, 0, 0)
        left.addWidget(heading)
        self._scope_note = _label("", f"font-size: 13px; color: {p['text_secondary']};")
        self._scope_note.setWordWrap(True)
        self._scope_note.setContentsMargins(6, 0, 6, 6)
        left.addWidget(self._scope_note)
        cards_host = QWidget()
        cards_host.setObjectName("clear")
        self._cards_layout = QVBoxLayout(cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(0)
        self._cards_layout.addStretch(1)
        left.addWidget(_scroll_area(cards_host), stretch=1)
        left_widget = QWidget()
        left_widget.setObjectName("clear")
        left_widget.setLayout(left)
        left_widget.setFixedWidth(312)  # a 300px card + 6px of shadow room each side
        outer.addWidget(left_widget)

        outer.addWidget(self._build_detail(), stretch=1)

        self._poller = PollingTimer(self._fetch, interval_ms=SHIPMENT_POLL_INTERVAL_MS, parent=self)
        self._poller.result_ready.connect(self._on_fetched)
        self.reload()

    # --- lifecycle -----------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._poller.start()
        if not self._intro_played:
            self._intro_played = True
            self._play_intro()

    def hideEvent(self, event) -> None:
        self._poller.stop()
        super().hideEvent(event)

    def _play_intro(self) -> None:
        """First time the page is on screen: cards and rows rise in."""
        stagger_in([self._card_holders[s.id] for s in self._shipments if s.id in self._card_holders], step=70)
        fade_in(self._head)
        stagger_in(list(self._rows.values()), step=45)

    # --- detail panel ----------------------------------------------------

    def _build_detail(self) -> QWidget:
        p = ORGANIC_PALETTE
        panel = _Panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._head = QWidget()
        self._head.setObjectName("clear")
        head = QHBoxLayout(self._head)
        head.setContentsMargins(28, 24, 28, 18)
        head.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._from_label = _label("", f"font-size: 15px; color: {p['text_secondary']};")
        self._title_label = _label(
            "", f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 32px; color: {p['text_primary']};"
        )
        self._title_label.setWordWrap(True)
        titles.addWidget(self._from_label)
        titles.addWidget(self._title_label)
        head.addLayout(titles, stretch=1)

        self._track = _SegmentTrack()
        track_layout = QHBoxLayout(self._track)
        track_layout.setContentsMargins(6, 6, 6, 6)
        track_layout.setSpacing(6)
        self._accept_button = _SegmentButton(tr("pos.receive.accept_all"), icons.CHECK, p["accent_2"], "white", "#3d472b")
        self._accept_button.clicked.connect(self._accept_all)
        self._report_button = _SegmentButton(tr("pos.receive.report"), icons.WARNING, _YELLOW, "#3a2a05", _AMBER_TEXT)
        self._report_button.clicked.connect(self._toggle_report)
        track_layout.addWidget(self._accept_button)
        track_layout.addWidget(self._report_button)
        head.addWidget(self._track, alignment=Qt.AlignBottom)
        layout.addWidget(self._head)

        self._columns = QWidget()
        self._columns.setObjectName("clear")
        columns = QHBoxLayout(self._columns)
        columns.setContentsMargins(28, 0, 28, 10)
        columns.setSpacing(12)
        for column, width, align in (("", 64, Qt.AlignLeft), ("product", 0, Qt.AlignLeft),
                                     ("expected", 110, Qt.AlignRight), ("received", 190, Qt.AlignRight)):
            label = _label(
                upper(tr(f"pos.receive.col_{column}")) if column else "",
                f"font-size: 13px; font-weight: 700; letter-spacing: 1px; color: {p['text_secondary']};",
            )
            label.setAlignment(align | Qt.AlignVCenter)
            if width:
                label.setFixedWidth(width)
                columns.addWidget(label)
            else:
                columns.addWidget(label, stretch=1)
        layout.addWidget(self._columns)

        rows_host = QWidget()
        rows_host.setObjectName("clear")
        self._rows_layout = QVBoxLayout(rows_host)
        self._rows_layout.setContentsMargins(16, 0, 16, 0)
        self._rows_layout.setSpacing(6)
        self._rows_layout.addStretch(1)
        layout.addWidget(_scroll_area(rows_host), stretch=1)

        self._note_input = QLineEdit()
        self._note_input.setPlaceholderText(tr("pos.receive.note_placeholder"))
        self._note_input.setStyleSheet(
            f"background-color: {p['background']}; color: {p['text_primary']}; border: none; border-radius: 22px; "
            f"padding: 10px 18px; font-size: 15px; margin: 8px 28px;"
        )
        layout.addWidget(self._note_input)

        self._message = _label("", f"font-size: 14px; color: {p['text_secondary']}; padding: 4px 28px 8px 28px;")
        self._message.setWordWrap(True)
        self._message.hide()
        layout.addWidget(self._message)

        self._footer = _Footer()
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(28, 18, 28, 18)
        footer_layout.setSpacing(16)
        self._count_label = _CountLabel()
        self._count_label.setStyleSheet(f"{_CLEAR} font-size: 17px; color: {p['text_primary']};")
        footer_layout.addWidget(self._count_label, stretch=1)
        self._complete_button = _CompleteButton()
        self._complete_button.clicked.connect(self._complete)
        footer_layout.addWidget(self._complete_button)
        layout.addWidget(self._footer)
        return panel

    def _say(self, text: str) -> None:
        self._message.setText(text)
        self._message.setVisible(bool(text))

    def _notify(self, text: str) -> None:
        """Success feedback: the main window's own notification if it has one, else a toast."""
        window = self.window()
        notify = getattr(window, "notify", None) if window is not self else None
        if callable(notify):
            notify(text)
        else:
            toast(self, text, 2600, style=toast_style())

    # --- data ---------------------------------------------------------

    def _fetch(self) -> list[Shipment] | None:
        try:
            shipments = shipment_repository.list_shipments(
                statuses=("scheduled", "in_transit", "delivered"), dealership_code=self._code
            )
        except Exception:  # transient lock on a timer tick
            return None
        cutoff = datetime.now(timezone.utc) - _RECENT
        return [
            s for s in shipments
            if s.is_active or (s.delivered_at and parse_db_timestamp(s.delivered_at) >= cutoff)
        ]

    def reload(self) -> None:
        self._on_fetched(self._fetch())

    def _on_fetched(self, shipments: list[Shipment] | None) -> None:
        if shipments is None:
            return
        # Still-coming ones first (soonest ETA), received ones after.
        self._shipments = [s for s in shipments if s.is_active] + [s for s in shipments if not s.is_active]
        ids = {s.id for s in self._shipments}
        if self._selected_id not in ids:
            self._selected_id = self._shipments[0].id if self._shipments else None
        if self._code is None:
            self._scope_note.setText(tr("pos.receive.scope_unlinked"))
        else:
            self._scope_note.setText(tr("pos.receive.scope_linked").format(name=self._dealer_name or self._code))
        self._render()
        self.incoming_changed.emit()

    def shipments(self) -> list[Shipment]:
        return list(self._shipments)

    def selected(self) -> Shipment | None:
        return next((s for s in self._shipments if s.id == self._selected_id), None)

    def select(self, shipment_id: int) -> None:
        self._selected_id = shipment_id
        self._render()

    # --- per-shipment working state ------------------------------------

    @staticmethod
    def _can_receive(shipment: Shipment) -> bool:
        """Only a shipment that has left the warehouse (in transit) can be checked in.
        A scheduled one is still at the depot: receiving it would create stock from nothing."""
        return shipment.status == "in_transit"

    def _received_for(self, shipment: Shipment, barcode: str) -> int:
        line = next(l for l in shipment.lines if l.product_barcode == barcode)
        if line.received_qty is not None:
            return line.received_qty
        return self._received.get(shipment.id, {}).get(barcode, line.expected_qty)

    def _is_checked(self, shipment: Shipment, barcode: str) -> bool:
        return shipment.status == "delivered" or barcode in self._checked.get(shipment.id, set())

    def toggle_line(self, barcode: str) -> None:
        shipment = self.selected()
        if shipment is None or not self._can_receive(shipment):
            return
        checked = self._checked.setdefault(shipment.id, set())
        checked.symmetric_difference_update({barcode})
        self._render()

    def change_received(self, barcode: str, delta: int) -> None:
        shipment = self.selected()
        if shipment is None or not self._can_receive(shipment):
            return
        received = self._received.setdefault(shipment.id, {})
        line = next((l for l in shipment.lines if l.product_barcode == barcode), None)
        if line is None:
            return
        # Never more than was shipped (that would be stock from nothing); fewer is a "short" discrepancy.
        received[barcode] = min(line.expected_qty, max(0, self._received_for(shipment, barcode) + delta))
        self._checked.setdefault(shipment.id, set()).add(barcode)
        self._render()

    def _accept_all(self) -> None:
        shipment = self.selected()
        if shipment is None or not self._can_receive(shipment):
            return
        self._checked[shipment.id] = {l.product_barcode for l in shipment.lines}
        self._received[shipment.id] = {l.product_barcode: l.expected_qty for l in shipment.lines}
        self._report_mode[shipment.id] = False
        self._render()

    def _toggle_report(self) -> None:
        shipment = self.selected()
        if shipment is None or not self._can_receive(shipment):
            return
        self._report_mode[shipment.id] = not self._report_mode.get(shipment.id, False)
        self._render()

    def _complete(self) -> None:
        shipment = self.selected()
        if shipment is None or not self._can_receive(shipment):
            return
        received = {l.product_barcode: self._received_for(shipment, l.product_barcode) for l in shipment.lines}
        note = self._note_input.text() if self._report_mode.get(shipment.id) else None
        try:
            done = shipment_repository.complete_receipt(shipment.id, received, note, actor=current_session.actor())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._say(tr("pos.receive.failed").format(error=exc))
            self.reload()
            return
        self._note_input.clear()
        issues = len(done.discrepancies)
        text = tr("pos.receive.done_report" if issues else "pos.receive.done").format(number=done.number)
        self._say(text)
        self.reload()
        self._notify(text)
        self.stock_changed.emit()  # every receipt fills this shelf

    # --- rendering -------------------------------------------------------

    def _render(self) -> None:
        self._render_cards()
        self._render_detail()

    def _render_cards(self) -> None:
        ids = [s.id for s in self._shipments]
        if ids != list(self._card_holders):  # a different list: rebuild it (and let it rise in)
            while self._cards_layout.count() > 1:
                item = self._cards_layout.takeAt(0)
                if item.widget():
                    _discard(item.widget())
            self._card_holders.clear()
            self._cards.clear()
            for index, shipment in enumerate(self._shipments):
                holder = QWidget()
                holder.setObjectName("clear")
                holder_layout = QVBoxLayout(holder)
                holder_layout.setContentsMargins(6, 1, 6, 11)  # room for the card's shadow
                card = _ShipmentCard(shipment, shipment.id == self._selected_id)
                card.clicked.connect(lambda _c=False, sid=shipment.id: self.select(sid))
                holder_layout.addWidget(card)
                self._cards_layout.insertWidget(index, holder)
                self._card_holders[shipment.id] = holder
                self._cards[shipment.id] = card
            if self.isVisible():
                stagger_in(list(self._card_holders.values()), step=70)
            return
        for shipment in self._shipments:
            card = self._cards[shipment.id]
            card.update_shipment(shipment)
            card.set_selected(shipment.id == self._selected_id)

    def _clear_rows(self) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item.widget():
                _discard(item.widget())
        self._rows.clear()
        self._rows_key = None

    def _render_detail(self) -> None:
        shipment = self.selected()
        if shipment is None:
            self._clear_rows()
            self._from_label.setText("")
            name = self._dealer_name or tr("pos.receive.this_dealership")
            self._title_label.setText(tr("pos.receive.nothing_coming").format(name=name))
            self._count_label.configure("", "")
            self._count_label.setText("")
            for widget in (self._track, self._columns, self._footer, self._note_input):
                widget.hide()
            return
        for widget in (self._track, self._columns, self._footer):
            widget.show()

        delivered = shipment.status == "delivered"
        waiting = shipment.status == "scheduled"  # still at the depot: nothing to check in yet
        report = self._report_mode.get(shipment.id, False) and not delivered and not waiting
        accepted = (
            not report and not delivered and not waiting
            and self._checked.get(shipment.id) == {l.product_barcode for l in shipment.lines}
            and all(self._received_for(shipment, l.product_barcode) == l.expected_qty for l in shipment.lines)
        )

        key = (shipment.id, tuple((l.product_barcode, l.product_name, l.expected_qty) for l in shipment.lines))
        switched = key != self._rows_key
        animate = not switched
        self._from_label.setText(f"{shipment.origin}" + (" · " + tr("pos.receive.driver").format(driver=shipment.driver) if shipment.driver else "") +
                                 f" · {shipment.carrier}")
        self._title_label.setText(tr("pos.receive.shipment").format(number=shipment.number))
        self._accept_button.set_active(accepted, animate)
        self._report_button.set_active(report, animate)
        self._accept_button.setEnabled(not delivered and not waiting)
        self._report_button.setEnabled(not delivered and not waiting)
        self._note_input.setVisible(report)

        if switched:
            self._clear_rows()
            self._rows_key = key
            for index, line in enumerate(shipment.lines):
                row = _LineRow(line, self.toggle_line, self.change_received)
                self._rows[line.product_barcode] = row
                self._rows_layout.insertWidget(index, row)
        for line in shipment.lines:
            barcode = line.product_barcode
            self._rows[barcode].refresh(
                self._is_checked(shipment, barcode), self._received_for(shipment, barcode),
                line.expected_qty, report, animate,
            )
        if switched and self.isVisible():  # the new shipment's header and rows slide in
            fade_in(self._head)
            stagger_in(list(self._rows.values()), step=45)

        checked = sum(1 for l in shipment.lines if self._is_checked(shipment, l.product_barcode))
        issues = sum(1 for l in shipment.lines if self._received_for(shipment, l.product_barcode) != l.expected_qty)
        issue_note = f" · {plural('pos.receive.discrepancy', issues)}" if issues else ""
        sentinel = "\x01"
        template = tr("pos.receive.checked").format(checked=sentinel, total=len(shipment.lines))
        pre, _sep, post = template.partition(sentinel)
        self._count_label.configure(pre, post + f"<span style='color:{_RED_TEXT};font-weight:700'>{issue_note}</span>")
        if switched:
            self._count_label.setText(str(checked))
        else:
            count_up(self._count_label, str(checked))
        ready = self._can_receive(shipment) and checked == len(shipment.lines)
        self._complete_button.setText(  # "&&": a lone "&" is a keyboard-mnemonic marker
            tr("pos.receive.completed") if delivered
            else tr_or("pos.receive_waiting", "Not dispatched yet") if waiting
            else (tr("pos.receive.send_report") if issues else tr("pos.receive.complete"))
        )
        self._complete_button.setEnabled(ready)
