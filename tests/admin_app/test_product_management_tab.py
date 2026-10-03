"""GUI regression tests for admin_app.gui.product_management_tab.

Must skip cleanly when no display is available (headless CI / no X
server), per project convention - use pytest-qt's `qtbot` fixture and
mark with `@pytest.mark.skipif` on the absence of a display, or rely on
QT_QPA_PLATFORM=offscreen in the test environment.

TODO (next slice, once the tab is implemented):
- test_add_product_popup_refreshes_in_place_not_reopened
"""
