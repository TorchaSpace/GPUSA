"""Motion helpers: number parsing is exact, animations never change the final text."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

from admin_app.gui import motion


@pytest.mark.parametrize("text", ["131k", "1,234.50", "1.234,56", "40", "1.84M", "-12.5%", "1,234,567", "12,5"])
def test_plain_numbers_round_trip_exactly(text):
    parsed = motion._parse_number(text)
    assert parsed is not None
    assert motion._format_number(*parsed) == text


@pytest.mark.parametrize("text", ["—", "04.10.2026", "12 of 40", "", "no digits"])
def test_things_that_are_not_one_plain_number_are_left_alone(text):
    assert motion._parse_number(text) is None


def test_animations_are_off_on_the_test_platform(qapp):
    assert motion.animations_enabled() is False


def test_count_up_sets_the_text_at_once_when_motion_is_off(qapp):
    from PySide6.QtWidgets import QLabel

    label = QLabel("0")
    motion.count_up(label, "1,234.50")
    assert label.text() == "1,234.50"
    motion.count_up(label, "—")
    assert label.text() == "—"


def test_stat_card_value_is_final_immediately(qapp):
    from admin_app.gui.components.stat_card import StatCard

    card = StatCard("Revenue", "0")
    card.set_value("131k")
    assert card._value_label.text() == "131k"
    card.close()


def test_blend_endpoints():
    assert motion.blend("#000000", "#ffffff", 0.0) == "#000000"
    assert motion.blend("#000000", "#ffffff", 1.0) == "#ffffff"


def test_hover_filter_survives_events_after_its_state_is_cleared(qapp):
    """Qt can deliver events to a filter while its widget is torn down; that
    must never raise (it broke a test run with 'no attribute _widget')."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QWidget

    widget = QWidget()
    tween = motion.HoverTween(widget, lambda level: None)
    del tween._widget  # what a half-destroyed Python wrapper looks like
    del tween._apply
    assert tween.eventFilter(widget, QEvent(QEvent.Enter)) is False
    assert tween.eventFilter(widget, QEvent(QEvent.Leave)) is False
    widget.close()


def test_level_jumps_when_motion_is_off(qapp):
    from PySide6.QtWidgets import QWidget

    from admin_app.gui.motion import Level

    seen = []
    widget = QWidget()
    level = Level(widget, seen.append)
    level.go(1.0)
    assert level.value == 1.0 and seen == [1.0]


def test_animated_widgets_keep_their_state(qapp):
    from admin_app.gui.components.animated_checkbox import AnimatedCheckBox
    from admin_app.gui.components.segment_button import SegmentButton

    box = AnimatedCheckBox("x")
    box.setChecked(True)
    assert box.isChecked() and box._checked.value == 1.0
    box.setChecked(False)
    assert not box.isChecked() and box._checked.value == 0.0
    tab = SegmentButton("A & B")
    tab.setChecked(True)
    assert tab.isChecked() and tab._on.value == 1.0
    box.grab()
    tab.grab()


def test_toast_adds_and_replaces_a_label(qapp):
    from PySide6.QtWidgets import QWidget

    from admin_app.gui.motion import toast

    window = QWidget()
    window.resize(400, 300)
    toast(window, "Saved")
    first = window._motion_toast
    toast(window, "Again")
    assert window._motion_toast is not first
