"""A barcode / QR reader works wherever the cursor is on the POS: a read typed
outside every text box adds to the sale (or ticks a line on Receive Inventory)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

import database.connection as connection
from database import product_repository, shipment_repository as ships
from shared.models import Product


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def window(qapp, monkeypatch):
    import pos_app.gui.main_window as mw

    monkeypatch.setattr(mw, "load_dealership_identity", lambda: None)
    product_repository.create(Product("8691234510531", "Olive Oil", 10.0, 5, 1))
    win = mw.MainWindow(None)
    win._watcher.stop()
    notes = []
    monkeypatch.setattr(win, "notify", notes.append)
    win.notes = notes
    win.show()
    pump(qapp)
    yield win
    win.close()


def _read(widget, code):
    """What a USB reader does: type the code fast, then Enter - into `widget`."""
    widget.setFocus()
    QTest.keyClicks(widget, code)
    QTest.keyClick(widget, Qt.Key_Return)


def _cart(window):
    return {b: line.quantity for b, line in window._sale_page._cart.items()}


def test_a_read_on_the_home_page_adds_to_the_sale_and_opens_it(window, qapp):
    assert window._stack.currentWidget() is window._home_page
    _read(window._header, "8691234510531")
    pump(qapp)
    assert _cart(window) == {"8691234510531": 1}
    assert window._stack.currentWidget() is window._sale_page
    assert window.notes[-1] == "Olive Oil added to sale"


def test_a_gs1_qr_finds_the_ean13_product(window, qapp):
    _read(window._header, "https://id.gs1.org/01/08691234510531/10/LOT7")
    pump(qapp)
    assert _cart(window) == {"8691234510531": 1}


def test_an_unknown_read_says_so_and_adds_nothing(window, qapp):
    _read(window._header, "NOPE-1")
    pump(qapp)
    assert _cart(window) == {}
    assert window.notes[-1] == "No product found with this barcode"


def test_typing_in_a_text_box_is_left_alone(window, qapp):
    window._navigate("sale")
    pump(qapp)
    search = window._sale_page._search_input
    search.setFocus()
    QTest.keyClicks(search, "Olive")
    assert search.text() == "Olive"
    assert window._scan_catcher.pending() == ""


def test_the_open_keypad_keeps_its_keys(window, qapp):
    window.open_barcode_keypad()
    pump(qapp)
    QTest.keyClicks(window._keypad, "8691")
    assert window._keypad.code() == "8691"
    assert window._scan_catcher.pending() == ""


def test_a_read_on_receive_inventory_ticks_that_line(window, qapp):
    product_repository.create(Product("RICE-1", "Rice", 4.0, 20, 0))
    from database import dealership_repository
    from shared.models import Dealership

    dealership_repository.create(Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk"))
    s = ships.create("WH-01", "001", "Ridgeline", datetime.now() + timedelta(minutes=30), [("RICE-1", 6), ("8691234510531", 2)], "Murat")
    ships.dispatch(s.id)
    window._navigate("receive")
    window._receive_page.reload()
    pump(qapp)

    _read(window._header, "rice-1")
    pump(qapp)
    page = window._receive_page
    assert page._checked[s.id] == {"RICE-1"}
    assert window.notes[-1] == "Rice ticked (1 of 2)"
    assert _cart(window) == {}  # not added to a sale

    _read(window._header, "NOPE-1")
    pump(qapp)
    assert window.notes[-1] == '"NOPE-1" isn\'t in any incoming shipment'
