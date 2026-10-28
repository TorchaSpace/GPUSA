"""Entry point for the Depot app. Mirrors pos_app/main.py and admin_app/main.py's shape."""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from database import settings_repository
from database.connection import check_database, pin_db_path
from database.exceptions import DATABASE_ERRORS
from depot_app.gui.main_window import MainWindow
from depot_app.theme import INDUSTRY_PALETTE
from shared import currency, i18n
from shared.gui_kit.db_error import show_database_error
from shared.theme import apply_theme
from shared.warehouse_bootstrap import register_pending_warehouse
from database.backups import daily_backup


BACKUP_CHECK_MS = 60 * 60 * 1000


def main() -> int:
    # From here on this process keeps using THIS database file, even if Settings later points the
    # installation elsewhere (that takes effect at the next start, for every app alike).
    pin_db_path()
    # Apply this instance's Depot_N.warehouse.json (from setup) if Admin
    # hasn't already - see shared/warehouse_bootstrap.py. Never blocks startup.
    register_pending_warehouse()
    app = QApplication(sys.argv)
    apply_theme(app, palette=INDUSTRY_PALETTE)
    i18n.set_language(settings_repository.safe_language())  # the language chosen in Admin > Settings
    currency.set_currency(settings_repository.safe_currency())
    try:
        check_database()
        window = MainWindow()
    except DATABASE_ERRORS as exc:  # a damaged or locked file: say so, never crash without a window
        show_database_error(exc)
        return 1
    window.show()
    # Today's backup now, then an hourly check so a till left open for days still makes one a day.
    daily_backup()
    backup_timer = QTimer(window)
    backup_timer.timeout.connect(lambda: daily_backup())
    backup_timer.start(BACKUP_CHECK_MS)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
