"""Entry point for the Branch POS app. Mirrors admin_app/main.py's shape."""

import sys

from PySide6.QtWidgets import QApplication

from pos_app.gui.main_window import MainWindow
from pos_app.theme import ORGANIC_PALETTE
from pos_app.gui.auth_flow import till_sign_in_dialog
from database import settings_repository
from shared import currency, i18n
from shared.dealership_bootstrap import load_dealership_identity, register_pending_dealership
from shared.theme import apply_theme


def main() -> int:
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
    identity = load_dealership_identity()
    code = identity["code"] if identity else None
    dialog = till_sign_in_dialog(code, identity["name"] if identity else "GPUSA")
    if not dialog.exec() or dialog.session is None:
        return 0
    window = MainWindow(dialog.session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
