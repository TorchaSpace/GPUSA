"""Top-level POS window: PosHeader (brand/location/nav/cashier) above a
QStackedWidget of the 4 mockup screens (Home/New Sale/Receive/My Stock).

The cashier is whoever signed in at this till (pos_app/main.py runs the
sign-in before this window opens; the header's "Switch" hands over at
shift change - the open sale is cleared, the new cashier signs in, and
every sale records who rang it up). Built without a session (tests) it
shows the generic CASHIER_NAME.

`dealership_name`/`location_line` ARE real once this instance has
registered, though: shared.dealership_bootstrap.load_dealership_identity()
reads this terminal's own sidecar file (see that module's docstring) and,
if present, replaces the DEALERSHIP_NAME/LOCATION_LINE placeholders below
with the terminal's actual registered name and city/region - a real-but-
different substitution for "Main Floor · Register 01" (no floor/register
concept exists in the schema), same principle as depot_app Console's
Warehouses page showing real SKU stats instead of the mockup's fabricated
capacity numbers. Falls back to the placeholders below whenever there's
nothing to show (dev-mode run, no sidecar, not yet registered).
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from database import account_repository
from database.exceptions import DataAccessError
from pos_app.gui.auth_flow import till_sign_in_dialog

from pos_app.gui.components.organic import toast_style
from pos_app.gui.components.pos_header import PosHeader
from pos_app.gui.pages.home_page import HomePage
from pos_app.gui.pages.my_stock_page import MyStockPage
from pos_app.gui.pages.new_sale_page import NewSalePage
from pos_app.gui.pages.receive_page import ReceivePage
from pos_app.gui.sales_page import SalesPage
from pos_app.theme import ORGANIC_PALETTE
from shared import current_session
from shared.auth import IDLE_LOCK_SECONDS, Session
from shared.dealership_bootstrap import load_dealership_identity
from shared.gui_kit.idle_lock import IdleLock
from shared.gui_kit.motion import animations_enabled, fade_in, toast
from shared.i18n import tr
from shared.models import UNASSIGNED, StockLocation

DEALERSHIP_NAME = "GPUSA"
LOCATION_LINE = "Main Floor · Register 01"  # English reference; the window shows tr("pos.main.location_line")
CASHIER_NAME = "Cashier"  # English reference; the window shows tr("pos.main.cashier")


class MainWindow(QMainWindow):
    def __init__(self, session: Session | None = None):
        super().__init__()
        self.session = session
        current_session.set(session)
        self.setWindowTitle(tr("pos.window_title"))
        # Till left alone for IDLE_LOCK_SECONDS: hand it over as at a shift change.
        self._idle_lock = IdleLock(IDLE_LOCK_SECONDS, parent=self)
        self._idle_lock.idle.connect(self._lock_when_idle)
        if session is not None:
            self._idle_lock.start()
        self.resize(1280, 800)
        self.setStyleSheet(f"QMainWindow {{ background-color: {ORGANIC_PALETTE['background']}; }}")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        identity = load_dealership_identity()
        dealership_name = identity["name"] if identity else DEALERSHIP_NAME
        location_line = identity["location_line"] if identity else tr("pos.main.location_line")

        self._dealership_name = dealership_name
        self._header = PosHeader(dealership_name, location_line, session.name if session else tr("pos.main.cashier"))
        self._header.switch_cashier.connect(self.switch_cashier)
        layout.addWidget(self._header)

        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}

        cashier_first_name = session.first_name if session else tr("pos.main.cashier").split()[0]
        # The dealership this terminal was set up as (None in a dev run /
        # a POS with no setup file) - scopes Receive Inventory and its badge.
        dealership_code = identity["code"] if identity else None
        self._dealership_code = dealership_code
        # ...and whose shelf it sells from / receives into. Without an
        # identity it works on unassigned stock (see stock_repository).
        location = StockLocation.dealership(dealership_code) if dealership_code else UNASSIGNED
        self._home_page = HomePage(cashier_first_name, dealership_code=dealership_code)
        self._home_page.tile_clicked.connect(self._navigate)
        self._register_page("home", self._home_page)

        self._sale_page = NewSalePage(location)
        self._register_page("sale", self._sale_page)

        self._stock_page = MyStockPage(location)
        self._register_page("stock", self._stock_page)

        self._receive_page = ReceivePage(dealership_code, identity["name"] if identity else None)
        self._receive_page.stock_changed.connect(self._stock_page.reload)
        self._receive_page.stock_changed.connect(self._home_page.reload_badges)
        self._receive_page.incoming_changed.connect(self._home_page.reload_badges)
        self._register_page("receive", self._receive_page)

        self._sales_page = SalesPage(location)
        self._sales_page.stock_changed.connect(lambda: self._stock_page.reload(play=False))
        self._register_page("sales", self._sales_page)

        layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        self._header.page_selected.connect(self._navigate)
        self._navigate("home")

    def _lock_when_idle(self) -> None:
        if self.session is not None and self.isVisible():
            self.switch_cashier()

    def switch_cashier(self) -> None:
        """Shift change: sign the current cashier out, clear the open sale,
        and ask the next one to sign in. "Close till" there closes the POS."""
        if self.session is not None:
            try:
                account_repository.sign_out(self.session)
            except DataAccessError:
                pass
        self.session = None
        current_session.clear()
        self._sale_page.clear_cart()
        self.hide()
        dialog = till_sign_in_dialog(self._dealership_code, self._dealership_name)
        if dialog.exec() and dialog.session is not None:
            self.set_session(dialog.session)
            self._navigate("home")
            self._idle_lock.touch()
            self.show()
        else:
            QApplication.quit()

    def set_session(self, session: Session) -> None:
        self.session = session
        current_session.set(session)
        self._header.set_cashier(session.name)
        self._home_page.set_cashier(session.first_name)

    def _register_page(self, key: str, widget: QWidget) -> None:
        self._pages[key] = widget
        self._stack.addWidget(widget)

    def _navigate(self, key: str) -> None:
        widget = self._pages.get(key)
        if widget is None:
            return
        previous = self._stack.currentWidget()
        self._stack.setCurrentWidget(widget)
        self._header.set_active(key)
        # Home plays its own staggered intro; every other page fades and
        # drifts up a few pixels. Nothing animates before the window is up
        # or when the page didn't actually change.
        if key != "home" and widget is not previous and self.isVisible():
            self._animate_in(widget)
        if key == "home":
            self._home_page.reload_badges()
        elif key == "sale":
            self._sale_page.reload()
        elif key == "stock":
            self._stock_page.reload()
        elif key == "receive":
            self._receive_page.reload()

    def _animate_in(self, widget: QWidget, rise: int = 14, duration: int = 240) -> None:
        if not animations_enabled():
            return
        fade_in(widget, duration)
        running = getattr(widget, "_page_slide", None)
        if running is not None:
            running.stop()
        end = QPoint(0, 0)  # stacked pages always rest at the stack's origin
        slide = QPropertyAnimation(widget, b"pos", widget)
        slide.setDuration(duration)
        slide.setStartValue(QPoint(end.x(), end.y() + rise))
        slide.setEndValue(end)
        slide.setEasingCurve(QEasingCurve.OutCubic)
        widget._page_slide = slide
        slide.start()

    def notify(self, text: str) -> None:
        """A short confirmation toast over the window (pages may call
        `self.window().notify(...)` when it exists)."""
        toast(self, text, 2600, style=toast_style())
