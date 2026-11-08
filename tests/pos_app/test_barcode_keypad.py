"""Manual barcode entry: the header's keypad button opens the overlay, a typed code goes into the
sale (and the New Sale page comes forward); unknown / sold-out / maxed codes stay open with the reason."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import product_repository, stock_repository
from shared.models import UNASSIGNED, Product

SHELF = UNASSIGNED  # a window with no setup identity sells from unassigned stock


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def window(qapp, monkeypatch):
    import pos_app.gui.main_window as mw

    monkeypatch.setattr(mw, "load_dealership_identity", lambda: None)
    product_repository.create(Product("8691234510531", "Olive Oil", 10.0, 2, 1))
    product_repository.create(Product("RICE-1", "Rice", 4.0, 0, 0))  # nothing on the shelf
    product_repository.create(Product("8690000000001", "Retired", 1.0, 5, 0))
    product_repository.set_active("8690000000001", False)
    win = mw.MainWindow(None)
    win._watcher.stop()
    notes = []
    monkeypatch.setattr(win, "notify", notes.append)
    win.notes = notes
    win.show()
    pump(qapp)
    yield win
    win.close()


def _type(keypad, digits):
    for d in digits:
        keypad.key_buttons[d].click()


def test_header_button_opens_the_keypad_over_the_whole_window(window, qapp):
    keypad = window._keypad
    assert not keypad.isVisible()
    window._header.keypad_button.click()
    pump(qapp)
    assert keypad.isVisible()
    assert keypad.geometry() == window.rect()
    assert keypad.code() == "" and not keypad.submit_button.isEnabled()
    assert keypad.code_label.text() == "0000 0000 0000 0"
    assert keypad.message_label.text() == "Type the number printed under the barcode"


def test_keys_type_group_by_four_backspace_and_clear(window, qapp):
    keypad = window._keypad
    window.open_barcode_keypad()
    _type(keypad, "869123")
    assert keypad.code() == "869123" and keypad.code_label.text() == "8691 23"
    assert keypad.length_label.text() == "6 / 13" and keypad.submit_button.isEnabled()
    keypad.key_buttons["⌫"].click()
    assert keypad.code() == "86912"
    keypad.key_buttons["C"].click()
    assert keypad.code() == "" and not keypad.submit_button.isEnabled()


def test_a_known_code_goes_into_the_sale_and_opens_new_sale(window, qapp):
    keypad = window._keypad
    window.open_barcode_keypad()
    _type(keypad, "8691234510531")
    keypad.submit_button.click()
    pump(qapp)
    assert not keypad.isVisible()
    assert window._stack.currentWidget() is window._sale_page
    assert window._sale_page._cart["8691234510531"].quantity == 1
    assert window.notes == ["Olive Oil added to sale"]


def test_refused_codes_keep_the_keypad_open_with_the_reason(window, qapp):
    keypad = window._keypad
    window.open_barcode_keypad()
    _type(keypad, "999")
    keypad.submit()
    assert keypad.isVisible() and keypad.error() == "No product found with this barcode"
    assert keypad.message_label.text() == "No product found with this barcode"
    assert keypad.code() == "999"  # left in place to fix
    keypad.key_buttons["1"].click()
    assert keypad.error() == ""  # typing clears the message

    keypad.clear()
    _type(keypad, "8690000000001")  # deactivated: can't be sold
    keypad.submit()
    assert keypad.error() == "No product found with this barcode"

    keypad.clear()
    keypad.type_text("rice-1")  # SKU-style code from a real keyboard, any case
    keypad.submit()
    assert keypad.error() == "Rice is out of stock"
    assert not window._sale_page.has_items()


def test_cannot_add_more_than_the_shelf_holds(window, qapp):
    keypad = window._keypad
    for _ in range(2):
        window.open_barcode_keypad()
        keypad.type_text("8691234510531")
        keypad.submit()
    window.open_barcode_keypad()
    keypad.type_text("8691234510531")
    keypad.submit()
    assert keypad.isVisible() and keypad.error() == "No more Olive Oil left in stock"
    assert window._sale_page._cart["8691234510531"].quantity == 2


def test_physical_keyboard_enter_escape(window, qapp):
    keypad = window._keypad
    window.open_barcode_keypad()
    pump(qapp)
    QTest.keyClicks(keypad, "8691234510531")
    assert keypad.code() == "8691234510531"
    QTest.keyClick(keypad, Qt.Key_Backspace)
    assert keypad.code() == "869123451053"
    QTest.keyClick(keypad, Qt.Key_1)
    QTest.keyClick(keypad, Qt.Key_Return)
    pump(qapp)
    assert not keypad.isVisible() and window._sale_page.has_items()

    window.open_barcode_keypad()
    QTest.keyClick(keypad, Qt.Key_Escape)
    assert not keypad.isVisible()


def test_close_button_and_scrim_close(window, qapp):
    keypad = window._keypad
    window.open_barcode_keypad()
    keypad.close_button.click()
    assert not keypad.isVisible()
    window.open_barcode_keypad()
    pump(qapp)
    QTest.mouseClick(keypad, Qt.LeftButton, pos=keypad.rect().topLeft() + QPoint(5, 5))  # the scrim, well outside the card
    assert not keypad.isVisible()


def test_turkish_strings(window, qapp):
    from shared import i18n

    previous = i18n.current_language()
    i18n.set_language("tr")
    try:
        added, message = window._sale_page.add_by_barcode("0000")
        assert not added and message == "Bu barkodla ürün bulunamadı"
    finally:
        i18n.set_language(previous)
