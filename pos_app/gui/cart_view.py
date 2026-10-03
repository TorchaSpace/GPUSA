"""Current cart/receipt view - the visual for the POS main window's VisualTab.

TODO (next slice): a table/list of scanned LineItems + running total,
built as a shared.gui_kit.VisualTab subclass so the "Complete Sale"
button (pos_app/gui/components/action_button.py) sits in its controls
area, in normal document flow.
"""

from __future__ import annotations

from shared.gui_kit.visual_tab import VisualTab


class CartView(VisualTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        raise NotImplementedError
