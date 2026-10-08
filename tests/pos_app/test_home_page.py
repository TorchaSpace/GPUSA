"""Offscreen GUI tests for pos_app's Home screen: the three tiles report which
one was tapped, and the badges show the live counts. (Animations are off under
the offscreen platform, so every state is final immediately.)"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.gui_support import pump, qapp  # noqa: F401  (qapp is a fixture)

from shared.models import Product


@pytest.fixture
def home(qapp, monkeypatch):
    import pos_app.gui.pages.home_page as module

    products = [
        Product("A", "Out of stock", 1.0, 0, 2),
        Product("B", "Low", 1.0, 1, 2),
        Product("C", "Fine", 1.0, 20, 2),
        Product("D", "Also out", 1.0, 0, 5),
    ]
    monkeypatch.setattr(module.stock_repository, "products_at", lambda location: products)
    monkeypatch.setattr(module.shipment_repository, "count_incoming", lambda code: 3)
    page = module.HomePage("Selin")
    page.resize(1280, 700)
    page.show()
    pump(qapp)
    yield page
    page.close()


def test_badges_show_counts(home):
    assert home._receive_badge.text().startswith("3")
    assert home._out_badge.text().startswith("2")
    assert home._low_badge.text().startswith("1")


@pytest.mark.parametrize("attr,key", [("_sale_tile", "sale"), ("_receive_tile", "receive"), ("_stock_tile", "stock")])
def test_tiles_emit_their_key(home, attr, key):
    seen = []
    home.tile_clicked.connect(seen.append)
    QTest.mouseClick(getattr(home, attr), Qt.LeftButton)
    assert seen == [key]


def test_sale_tile_decor_tracks_size(home):
    tile = home._sale_tile
    cx, cy, r, _tone = tile._decor[0]
    assert r == 130
    assert cx * tile.width() == pytest.approx(tile.width() - 60)
    assert cy * tile.height() == pytest.approx(60)
