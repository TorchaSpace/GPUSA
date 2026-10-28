"""Entry point for the Branch POS app. Mirrors admin_app/main.py's shape."""

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pos_app.gui.main_window import MainWindow
from pos_app.theme import ORGANIC_PALETTE
from pos_app.gui.auth_flow import till_sign_in_dialog
from database import dealership_repository, settings_repository
from database.connection import check_database, pin_db_path
from database.exceptions import DATABASE_ERRORS
from shared import currency, i18n
from shared.dealership_bootstrap import load_dealership_identity, register_pending_dealership
from shared.gui_kit.db_error import show_database_error
from shared.theme import apply_theme
from database.backups import daily_backup


BACKUP_CHECK_MS = 60 * 60 * 1000


def main() -> int:
    # From here on this process keeps using THIS database file, even if Settings later points the
    # installation elsewhere (that takes effect at the next start, for every app alike).
    pin_db_path()
    # No-op unless this instance was built by deploy_system.py/installer.py
    # with a dealership sidecar file next to it - see
    # shared/dealership_bootstrap.py for what this does and why it can
    # never block the app from opening.
    register_pending_dealership()

    app = QApplication(sys.argv)
    i18n.set_language(settings_repository.safe_language())
    currency.set_currency(settings_repository.safe_currency())
    apply_theme(app, palette=ORGANIC_PALETTE)
    # A cashier signs in before the till opens (pos_app/gui/auth_flow.py).
    try:
        check_database()
        identity = load_dealership_identity()
        code = identity["code"] if identity else None
        if code:
            code = dealership_repository.canonical_code(code)
        dialog = till_sign_in_dialog(code, identity["name"] if identity else "GPUSA")
        if not dialog.exec() or dialog.session is None:
            return 0
        window = MainWindow(dialog.session)
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
