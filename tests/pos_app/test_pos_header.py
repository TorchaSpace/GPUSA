"""POS header: the shop drop-down must not pretend a till with no shop has one."""

from __future__ import annotations

from tests.gui_support import qapp  # noqa: F401

from pos_app.gui.components.pos_header import PosHeader


def test_till_without_a_shop_shows_a_prompt_not_the_first_shop(qapp):
    header = PosHeader("-", "Main Floor", "Cashier")
    header.set_dealership_choices([("D1", "Alpha"), ("D2", "Beta")], None)
    assert header._shop_picker.currentIndex() == -1
    assert header._shop_picker.currentData() is None


def test_till_with_a_shop_selects_it(qapp):
    header = PosHeader("Beta", "Main Floor", "Cashier")
    header.set_dealership_choices([("D1", "Alpha"), ("D2", "Beta")], "D2")
    assert header._shop_picker.currentData() == "D2"
