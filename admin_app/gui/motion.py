"""Small, quiet motion for the Admin app: a short fade when a page or a
dialog appears, numbers that count up to their value, and a border that
warms up under the mouse.

Rules this module keeps so the app never feels slower or flaky:

* Every animation is under ~0.5 s and uses an ease-out curve.
* The text/state a caller sets is final immediately for everything except
  what is on screen: `count_up` shows the target text at the end, and the
  fade removes its effect afterwards (a lingering QGraphicsOpacityEffect
  makes text blurry and slows scrolling).
* Motion is skipped - the end state is applied at once - when
  `animations_enabled()` is False: the offscreen test platform, or the user
  setting GPUSA_REDUCE_MOTION=1 (also honoured: the OS "reduce motion"
  switch is not exposed by Qt, so this is the opt-out).
"""

from __future__ import annotations

import os
import re

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QVariantAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QGraphicsOpacityEffect, QLabel, QMessageBox, QWidget

FADE_MS = 170
COUNT_MS = 480
HOVER_MS = 140

_KEEP = "_motion_animations"  # attribute holding live animations so Python doesn't drop them


def animations_enabled() -> bool:
    if os.environ.get("GPUSA_REDUCE_MOTION", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    app = QApplication.instance()
    if app is None:
        return False
    return app.platformName() not in {"offscreen", "minimal", "minimalegl"}


def _remember(widget: QObject, animation: QVariantAnimation) -> None:
    live = getattr(widget, _KEEP, None)
    if live is None:
        live = []
        setattr(widget, _KEEP, live)
    live.append(animation)
    animation.finished.connect(lambda: live.remove(animation) if animation in live else None)


def fade_in(widget: QWidget, duration: int = FADE_MS) -> None:
    """Fade `widget` from transparent to opaque once, then drop the effect."""
    if widget is None or not animations_enabled():
        return
    previous = getattr(widget, "_motion_fade", None)
    if previous is not None:
        previous.stop()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)

    def done() -> None:
        # Only clear our own effect (another fade may have replaced it).
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        widget._motion_fade = None

    animation.finished.connect(done)
    widget._motion_fade = animation
    animation.start()


# --- numbers --------------------------------------------------------------------

_NUMBER = re.compile(r"^(?P<pre>[^\d\-+]*)(?P<sign>[\-+]?)(?P<num>\d[\d.,]*)(?P<post>.*)$")


def _parse_number(text: str):
    """Split "1,234.50k" into (prefix, value, decimals, grouping, decimal_mark, suffix),
    or None when the text isn't a plain number we can animate safely."""
    match = _NUMBER.match(text.strip())
    if not match:
        return None
    num = match["num"]
    last_dot, last_comma = num.rfind("."), num.rfind(",")
    decimal_mark = ""
    if last_dot >= 0 and last_comma >= 0:
        decimal_mark = "." if last_dot > last_comma else ","
    elif last_dot >= 0 and num.count(".") == 1 and len(num) - last_dot - 1 != 3:
        decimal_mark = "."
    elif last_comma >= 0 and num.count(",") == 1 and len(num) - last_comma - 1 != 3:
        decimal_mark = ","
    grouping = ""
    for mark in (",", "."):
        if mark != decimal_mark and mark in num:
            grouping = mark
    if grouping == "" and decimal_mark == "" and ("," in num or "." in num):
        return None  # ambiguous like "1.234" / "1,234": show it as is
    int_part = num.split(decimal_mark)[0] if decimal_mark else num
    if grouping and not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(grouping)}\d{{3}})+", int_part):
        return None  # e.g. a date like 04.10.2026
    if re.search(r"\d", match["post"]):
        return None  # "12 of 40", "3 / 5": more than one number - leave alone
    clean = num.replace(grouping, "") if grouping else num
    if decimal_mark:
        clean = clean.replace(decimal_mark, ".")
    try:
        value = float(clean)
    except ValueError:
        return None
    decimals = len(num) - num.rfind(decimal_mark) - 1 if decimal_mark else 0
    return match["pre"] + match["sign"], value, decimals, grouping, decimal_mark, match["post"]


def _format_number(prefix: str, value: float, decimals: int, grouping: str, decimal_mark: str, suffix: str) -> str:
    body = f"{abs(value):,.{decimals}f}"  # 1,234.50
    body = body.replace(",", "\x00").replace(".", decimal_mark or ".").replace("\x00", grouping)
    return f"{prefix}{body}{suffix}"


def count_up(label: QLabel, text: str, duration: int = COUNT_MS) -> None:
    """Set `label` to `text`, counting the number up to it from the number it
    showed before (or from zero) when both are plain numbers; otherwise just
    set the text."""
    previous = getattr(label, "_motion_count", None)
    if previous is not None:
        previous.stop()
        label._motion_count = None
    target = _parse_number(text)
    if target is None or not animations_enabled() or not label.isVisible():
        label.setText(text)
        return
    old = _parse_number(label.text())
    start = old[1] if old is not None and old[0] == target[0] and old[5] == target[5] else 0.0
    end = target[1]
    if start == end:
        label.setText(text)
        return
    prefix, _, decimals, grouping, decimal_mark, suffix = target
    animation = QVariantAnimation(label)
    animation.setDuration(duration)
    animation.setStartValue(float(start))
    animation.setEndValue(float(end))
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.valueChanged.connect(
        lambda v: label.setText(_format_number(prefix, float(v), decimals, grouping, decimal_mark, suffix))
    )

    def done() -> None:
        label.setText(text)  # exactly what the caller asked for, byte for byte
        label._motion_count = None

    animation.finished.connect(done)
    label._motion_count = animation
    label.setText(_format_number(prefix, float(start), decimals, grouping, decimal_mark, suffix))
    animation.start()


# --- hover ------------------------------------------------------------------------


def blend(a: str, b: str, t: float) -> str:
    """Colour between two '#rrggbb' strings, t = 0..1."""
    ca, cb = QColor(a), QColor(b)
    mix = lambda x, y: round(x + (y - x) * t)  # noqa: E731
    return QColor(mix(ca.red(), cb.red()), mix(ca.green(), cb.green()), mix(ca.blue(), cb.blue())).name()


class HoverTween(QObject):
    """Animates a 0..1 `level` up on mouse enter and back down on leave, and
    tells `apply(level)` each step (the caller restyles itself)."""

    def __init__(self, widget: QWidget, apply, duration: int = HOVER_MS):
        super().__init__(widget)
        self._widget, self._apply, self._level = widget, apply, 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(duration)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._step)
        widget.setAttribute(Qt.WA_Hover, True)
        widget.installEventFilter(self)

    def _step(self, value) -> None:
        apply = getattr(self, "_apply", None)
        if apply is None:
            return
        self._level = float(value)
        apply(self._level)

    def _go(self, target: float) -> None:
        if getattr(self, "_apply", None) is None:
            return
        if not animations_enabled():
            self._level = target
            self._apply(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._level)
        self._animation.setEndValue(target)
        self._animation.start()

    def eventFilter(self, obj, event) -> bool:
        # While the widget is being torn down Qt can still deliver events to
        # this filter after PySide has already cleared our Python attributes.
        widget = getattr(self, "_widget", None)
        if widget is not None and obj is widget:
            if event.type() == QEvent.Enter:
                self._go(1.0)
            elif event.type() == QEvent.Leave:
                self._go(0.0)
        return False


# --- dialogs ----------------------------------------------------------------------


class _DialogFader(QObject):
    """Application-wide: every QDialog (except native-looking message boxes,
    which Qt styles itself) fades in when it first shows."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Show and isinstance(obj, QDialog) and not isinstance(obj, QMessageBox):
            if obj.isWindow() and not getattr(obj, "_motion_shown", False):
                obj._motion_shown = True
                obj.setWindowOpacity(0.0)
                animation = QPropertyAnimation(obj, b"windowOpacity", obj)
                animation.setDuration(FADE_MS)
                animation.setStartValue(0.0)
                animation.setEndValue(1.0)
                animation.setEasingCurve(QEasingCurve.OutCubic)
                animation.finished.connect(lambda: obj.setWindowOpacity(1.0))
                if animations_enabled():
                    obj._motion_open = animation
                    animation.start()
                else:
                    obj.setWindowOpacity(1.0)
        return False


def install_dialog_fade(app: QApplication) -> None:
    fader = _DialogFader(app)
    app.installEventFilter(fader)
    app._motion_dialog_fader = fader
