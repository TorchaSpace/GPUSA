"""SUPERSEDED - kept in place but unused, same "unused but not deleted"
precedent as admin_app's original legacy tab files (see architecture.md).
This session has no device_bash tool available to delete files on
Erol's machine, so this stub is left in the tree with its content
removed rather than actually removed.

Depot's real low-stock alert is now depot_app/gui/low_stock_banner.py's
LowStockBanner - a full-width banner embedded directly in
depot_app/gui/main_window.py's Floor kiosk window, not a separate tab
(the original design here was a QTabWidget-based MainWindow; the actual
UI redesign uses the Floor-kiosk/Console-popout shape instead - see
main_window.py's module docstring).
"""
