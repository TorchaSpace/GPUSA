"""Small, quiet motion shared by the Admin and Depot apps: a short fade when a page or a
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

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPropertyAnimation, QTimer, QVariantAnimation, Qt,
)
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


class Level(QObject):
    """A value that glides between 0 and 1 (checked, hovered, selected...)
    and tells `on_change(level)` at every step so the owner repaints."""

    def __init__(self, owner: QWidget, on_change, duration: int = HOVER_MS, initial: float = 0.0):
        super().__init__(owner)
        self.value = float(initial)
        self._on_change = on_change
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(duration)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._step)

    def _step(self, value) -> None:
        self.value = float(value)
        self._on_change(self.value)

    def go(self, target: float, animate: bool = True) -> None:
        self._animation.stop()
        if not animate or not animations_enabled():
            self.value = float(target)
            self._on_change(self.value)
            return
        self._animation.setStartValue(self.value)
        self._animation.setEndValue(float(target))
        self._animation.start()


def stagger_in(widgets, step: int = 55, duration: int = 260, rise: int = 18) -> None:
    """Bring `widgets` in one after another: each fades up from `rise` px below.
    Each widget's own graphics effect is replaced while it plays and removed after,
    so give shadows to an inner widget (or accept that the shadow returns afterwards)."""
    if not animations_enabled():
        return
    for index, widget in enumerate(widgets):
        if widget is None:
            continue
        QTimer.singleShot(index * step, lambda w=widget: _rise(w, duration, rise) if _alive(w) else None)


def _rise(widget: QWidget, duration: int, rise: int) -> None:
    kept = widget.graphicsEffect()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    fade = QPropertyAnimation(effect, b"opacity", widget)
    fade.setDuration(duration)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setEasingCurve(QEasingCurve.OutCubic)
    start = widget.pos()
    slide = QPropertyAnimation(widget, b"pos", widget)
    slide.setDuration(duration)
    slide.setStartValue(QPoint(start.x(), start.y() + rise))
    slide.setEndValue(start)
    slide.setEasingCurve(QEasingCurve.OutCubic)
    group = QParallelAnimationGroup(widget)
    group.addAnimation(fade)
    group.addAnimation(slide)

    def done() -> None:
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(kept)

    group.finished.connect(done)
    widget._motion_rise = group
    group.start()


# --- toast ---------------------------------------------------------------------------


def toast(parent: QWidget, text: str, ms: int = 2400, style: str | None = None) -> None:
    """A small confirmation that slides up from the bottom of `parent`'s
    window, waits, and fades away. Never blocks or takes focus."""
    if parent is None or not text:
        return
    window = parent.window()
    previous = getattr(window, "_motion_toast", None)
    if previous is not None:
        previous.deleteLater()
    label = QLabel(text, window)
    label.setObjectName("motionToast")
    label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    label.setStyleSheet(style or (
        "#motionToast { background-color: #26231f; color: #eae7e7; border: 1px solid #e1ad66;"
        " border-radius: 6px; padding: 9px 16px; font-size: 13px; }"
    ))
    label.adjustSize()
    x = max(8, (window.width() - label.width()) // 2)
    rest_y = window.height() - label.height() - 28
    label.move(x, rest_y)
    label.show()
    label.raise_()
    window._motion_toast = label
    if not animations_enabled():
        QTimer.singleShot(ms, label.deleteLater)
        return
    effect = QGraphicsOpacityEffect(label)
    label.setGraphicsEffect(effect)
    slide = QPropertyAnimation(label, b"pos", label)
    slide.setDuration(260)
    slide.setStartValue(QPoint(x, rest_y + 16))
    slide.setEndValue(QPoint(x, rest_y))
    slide.setEasingCurve(QEasingCurve.OutCubic)
    fade = QPropertyAnimation(effect, b"opacity", label)
    fade.setDuration(260)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    group = QParallelAnimationGroup(label)
    group.addAnimation(slide)
    group.addAnimation(fade)
    group.start()
    out = QPropertyAnimation(effect, b"opacity", label)
    out.setDuration(320)
    out.setStartValue(1.0)
    out.setEndValue(0.0)
    out.finished.connect(label.deleteLater)
    label._motion_keep = (group, out)
    QTimer.singleShot(ms, lambda: out.start() if _alive(label) else None)


def _alive(obj) -> bool:
    try:
        obj.objectName()
        return True
    except RuntimeError:
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
                    group = QParallelAnimationGroup(obj)
                    group.addAnimation(animation)
                    end = obj.pos()
                    slide = QPropertyAnimation(obj, b"pos", obj)
                    slide.setDuration(FADE_MS + 60)
                    slide.setStartValue(QPoint(end.x(), end.y() + 12))
                    slide.setEndValue(end)
                    slide.setEasingCurve(QEasingCurve.OutCubic)
                    group.addAnimation(slide)
                    obj._motion_open = group
                    group.start()
                else:
                    obj.setWindowOpacity(1.0)
        return False


def install_dialog_fade(app: QApplication) -> None:
    fader = _DialogFader(app)
    app.installEventFilter(fader)
    app._motion_dialog_fader = fader
