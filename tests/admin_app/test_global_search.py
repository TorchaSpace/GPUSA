"""Offscreen GUI tests for the header search dialog and the shared header controls."""

from __future__ import annotations

from tests.gui_support import pump, qapp  # noqa: F401

from admin_app.gui.components.global_search import GlobalSearchDialog
from shared.models import Dealership, Product, Warehouse


def _records():
    return (
        [Product("BOX", "Carton", 40, 300, 10)],
        [Dealership(code="001", name="Harbor Point", region="Coastal", city="Norfolk")],
        [Warehouse(code="WH-01", name="Gebze Depo", city="Gebze")],
        [],
    )


def test_typing_filters_and_choosing_emits_the_page(qapp):
    dialog = GlobalSearchDialog(records=_records())
    seen = []
    dialog.page_chosen.connect(seen.append)
    dialog.set_query("harbor")
    assert [h.key for h in dialog.hits()] == ["001"]
    dialog.choose(0)
    assert seen == ["dealerships"] and dialog.chosen.key == "001"


def test_no_match_says_so(qapp):
    dialog = GlobalSearchDialog(records=_records())
    dialog.set_query("zzzz")
    assert dialog.hits() == [] and dialog._note.text() == "No matches."
