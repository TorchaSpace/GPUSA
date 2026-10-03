"""Entry point for the Depot app. Mirrors pos_app/main.py and admin_app/main.py's shape."""

import sys

from PySide6.QtWidgets import QApplication

from depot_app.gui.main_window import MainWindow
from depot_app.theme import INDUSTRY_PALETTE
from shared.theme import apply_theme
from shared.warehouse_bootstrap import register_pending_warehouse


def main() -> int:
    # Apply this instance's Depot_N.warehouse.json (from setup) if Admin
    # hasn't already - see shared/warehouse_bootstrap.py. Never blocks startup.
    register_pending_warehouse()
    app = QApplication(sys.argv)
    apply_theme(app, palette=INDUSTRY_PALETTE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
