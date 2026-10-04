"""Entry point for the Admin Dashboard app. Mirrors pos_app/main.py's shape."""

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from shared.i18n import tr
from admin_app.gui.app_style import admin_extra_qss
from admin_app.gui.auth_flow import sign_in
from admin_app.gui.main_window import MainWindow
from admin_app.gui.motion import install_dialog_fade
from admin_app.theme import CLASSICAL_PALETTE
from shared.dealership_bootstrap import register_sidecars_beside_this_exe
from shared.warehouse_bootstrap import register_warehouse_sidecars_beside_this_exe
from database import settings_repository
from database.exceptions import DataAccessError
from shared import currency, i18n
from shared.theme import apply_theme


def main() -> int:
    # Apply every POS_N.dealership.json the setup wizard left in this
    # folder, so the POS terminals from that setup run show up as active
    # dealerships the moment Admin opens - without anyone having to launch
    # each POS terminal first. No-op in a dev run; never blocks startup.
    # See shared/dealership_bootstrap.py.
    register_sidecars_beside_this_exe()
    # Same for every Depot_N.warehouse.json -> Admin > Warehouses.
    register_warehouse_sidecars_beside_this_exe()

    app = QApplication(sys.argv)
    apply_theme(app, palette=CLASSICAL_PALETTE)
    app.setStyleSheet(app.styleSheet() + admin_extra_qss(CLASSICAL_PALETTE))
    install_dialog_fade(app)
    i18n.set_language(settings_repository.safe_language())
    currency.set_currency(settings_repository.safe_currency())
    # Nobody sees Admin without signing in (see admin_app/gui/auth_flow.py):
    # the first administrator is created here on a fresh system.
    try:
        session = sign_in()
    except DataAccessError as exc:
        QMessageBox.critical(None, i18n.tr("admin.main.db_error"), str(exc))
        return 1
    if session is None:
        return 0
    window = MainWindow(session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
