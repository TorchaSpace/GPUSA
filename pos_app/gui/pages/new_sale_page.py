"""New Sale (checkout) screen - the mockup's product grid + "Current sale"
cart, painted with the Organic building blocks and eased with the shared motion
helpers. This is the concrete demonstration of the "shared database
is the connection" answer: this screen and admin_app's Inventory page
both read/write the exact same `products` table through
database.product_repository - there's no separate sync mechanism,
they're just two GUIs over one database.

Real, end to end:
- Stock is THIS dealership's shelf (database.stock_repository.products_at
  the terminal's dealership - see pos_app/gui/main_window.py): the grid
  shows and caps at what's here, not what the company has elsewhere.
- Product grid: every product with its local quantity, filtered by the
  search box client-side (no `category` field on Product - see
  shared.models - so the mockup's category pills are dropped).
- Adding to cart is capped at the product's live stock_quantity, same
  as the mockup's Math.min(qty+1, p.stock).
- Checkout calls pos_app.services.checkout_service.complete_sale(),
  which calls database.transaction_repository.finalize_transaction()
  (task #56) - a real atomic sale: stock is decremented in the same
  database admin_app's Inventory page reads, so switching to that page
  (or another POS till hitting the same shared_backend.db) shows the
  new numbers immediately, no polling or push needed.

Motion (all skipped when shared.gui_kit.motion.animations_enabled() is False):
cards stagger in when the grid is built and pulse when added; cart rows grow
and fade in, flash when their product is added again, and count their numbers
up; the total counts up and warms for a moment; Clear / payments confirm with
the dark-pill toast.

Simplification: the mockup has separate "Card"/"Cash" buttons. Since
shared.models.Transaction has no payment-method field yet, both buttons
finalize the identical sale - the distinction is visual only for now,
not persisted. Extending Transaction with a real payment_method column
is a small, separate follow-up once that's actually needed.

The VAT line is display only (the mockup's 10% included in the price); no tax
is stored or charged by the database.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from database import account_repository, product_repository, stock_repository
from database.exceptions import (
    DATABASE_ERRORS,
    DealershipInactiveError,
    InsufficientStockError,
    PriceChangedError,
    ProductNotFoundError,
    SessionInvalidError,
)
from pos_app.gui import icons
from pos_app.gui.auth_flow import till_dealership_problem
from pos_app.gui.components.organic import OrganicCard, PillButton, soft_shadow, toast_style
from pos_app.gui.product_status import stock_status
from pos_app.services.checkout_service import complete_sale
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.currency import format_money
from shared.gui_kit.motion import Level, animations_enabled, blend, count_up, fade_in, stagger_in, toast
from shared.i18n import plural, tr
from shared.models import PAYMENT_METHODS, UNASSIGNED, LineItem, Product, StockLocation, Transaction
from shared import current_session
from shared.textcase import upper
from shared.warehousing import tr_or

_PAYMENT_KEYS = {"Card": "pos.sale.card", "Cash": "pos.sale.cash"}  # payment code -> label key
_STATUS_COLORS = {"out": ("#d8412f", "white"), "low": ("#f2c230", "#3a2a05")}

# Design tokens the palette does not carry (mockup's --color-neutral-* / accent-* steps).
_NEUTRAL_200 = "#eee7db"
_NEUTRAL_900 = "#2e2b25"
_ACCENT_100, _ACCENT_200, _ACCENT_300 = "#fff2eb", "#ffe1d0", "#ffc6a5"
_ACCENT_600, _ACCENT_700 = "#b2622d", "#8c491a"
_NEUTRAL_800 = "#474238"
_TINTS = [_ACCENT_200, "#e1eecc", "#dcd3c4", _ACCENT_300, "#ccdbb2"]  # accent-2-200 / neutral-300 / accent-2-300
_VAT_RATE = 0.10  # the mockup's "VAT incl." line: total - total / 1.1
_GRID_COLUMNS = 4
_STAGGER_LIMIT = 20  # cards beyond this just appear (a long catalogue must not take seconds to arrive)


def _alive(obj) -> bool:
    try:
        obj.objectName()
        return True
    except RuntimeError:
        return False


@dataclass
class _CartLine:
    barcode: str
    name: str
    unit_price: float
    quantity: int
    max_quantity: int

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.quantity, 2)


# --- small painted pieces --------------------------------------------------------


class _Surface(QWidget):
    """A plain filled panel (optionally rounded) that paints itself, so no
    style sheet has to touch the labels it holds. `top_left_only` rounds just
    that corner (the cart panel)."""

    def __init__(self, colour: str, radius: int = 0, top_left_only: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour, self._radius, self._top_left_only = colour, radius, top_left_only

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        path = QPainterPath()
        r = float(self._radius)
        if self._top_left_only and r > 0:
            path.moveTo(rect.left() + r, rect.top())
            path.lineTo(rect.right(), rect.top())
            path.lineTo(rect.right(), rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.lineTo(rect.left(), rect.top() + r)
            path.arcTo(QRectF(rect.left(), rect.top(), 2 * r, 2 * r), 180, -90)
            path.closeSubpath()
        elif r > 0:
            path.addRoundedRect(rect, r, r)
        else:
            path.addRect(rect)
        painter.fillPath(path, QColor(self._colour))


class _SearchPill(_Surface):
    """The 60px search pill: icon + frameless line edit, a soft ring warms in when focused."""

    def __init__(self, placeholder: str, parent: QWidget | None = None):
        p = ORGANIC_PALETTE
        super().__init__(p["surface_raised"], 30, parent=parent)
        self.setFixedHeight(60)
        soft_shadow(self, "sm")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 0, 22, 0)
        layout.setSpacing(12)
        glass = QLabel()
        glass.setFixedSize(24, 24)
        glass.setPixmap(icons.pixmap(icons.SEARCH, p["text_secondary"], 24))
        glass.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(glass)
        self.edit = QLineEdit()
        self.edit.setObjectName("saleSearch")
        self.edit.setPlaceholderText(placeholder)
        self.edit.setStyleSheet(
            f"#saleSearch {{ border: none; background: transparent; font-family: {p['font_family_css']}; "
            f"font-size: 19px; color: {p['text_primary']}; }}"
        )
        layout.addWidget(self.edit, stretch=1)
        self._focus = Level(self, lambda _v: self.update(), 200)
        self.edit.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.edit:
            if event.type() == QEvent.FocusIn:
                self._focus.go(1.0)
            elif event.type() == QEvent.FocusOut:
                self._focus.go(0.0)
        return False

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._focus.value > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            ring = QColor(ORGANIC_PALETTE["accent"])
            ring.setAlpha(int(200 * self._focus.value))
            painter.setPen(ring)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 29, 29)


class _ElideLabel(QLabel):
    """A one-line label that clips with an ellipsis instead of forcing the row wider."""

    def __init__(self, text: str, colour: str, px: int, bold: bool, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._colour = colour
        font = QFont(self.font())
        font.setPixelSize(px)
        font.setWeight(QFont.Bold if bold else QFont.Normal)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(QFontMetrics(font).height() + 2)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.height())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(QColor(self._colour))
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.ElideRight, self.width())
        painter.drawText(QRectF(self.rect()), Qt.AlignVCenter | Qt.AlignLeft, text)


class _ProductCard(OrganicCard):
    """A product tile: the OrganicCard hover/press, plus a brief brightening
    flash when the product is added to the sale."""

    def __init__(self, bg: str, hover: str, press: str, dimmed: bool = False, parent: QWidget | None = None):
        super().__init__(bg, hover, press, radius=28, shadow="sm", parent=parent)
        self._pulse = Level(self, lambda _v: self.update(), 150)
        if dimmed:
            self._effect.setColor(QColor(46, 43, 37, 16))

    def pulse(self) -> None:
        self._pulse.go(1.0)
        QTimer.singleShot(110, lambda: self._pulse.go(0.0) if _alive(self) else None)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._pulse.value > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 28, 28)
            painter.fillPath(path, QColor(255, 255, 255, int(120 * self._pulse.value)))


class _CardSlot(QWidget):
    """Holds one product card. The grid animates the slot (an opacity effect
    would replace the card's own shadow), and the margins leave the shadow room."""

    def __init__(self, card: _ProductCard, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 10)
        layout.setSpacing(0)
        layout.addWidget(card)
        self.card = card


class _CartRow(QWidget):
    """One cart line: name / unit price, qty stepper pill, line total. Lives as
    long as the line does; `update_line` counts the numbers to their new values."""

    def __init__(self, line: _CartLine, on_change, parent: QWidget | None = None):
        super().__init__(parent)
        p = ORGANIC_PALETTE
        self.barcode = line.barcode
        self.setObjectName("cartRow")
        self._flash = Level(self, lambda _v: self.update(), 380)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(12)

        names = QVBoxLayout()
        names.setSpacing(0)
        self._name = _ElideLabel(line.name, p["text_primary"], 16, True)
        self._unit = _ElideLabel(self._unit_text(line), p["text_secondary"], 14, False)
        names.addWidget(self._name)
        names.addWidget(self._unit)
        layout.addLayout(names, stretch=1)

        stepper = _Surface(p["surface"], 26)
        stepper_layout = QHBoxLayout(stepper)
        stepper_layout.setContentsMargins(4, 4, 4, 4)
        stepper_layout.setSpacing(4)
        buttons = []
        for glyph, delta in (("−", -1), ("+", 1)):
            button = QPushButton(glyph)
            button.setObjectName("stepBtn")
            button.setFixedSize(40, 40)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                f"#stepBtn {{ border: none; border-radius: 20px; background-color: {p['surface_raised']}; "
                f"font-size: 20px; font-weight: 700; color: {p['text_primary']}; }}"
                f"#stepBtn:hover {{ background-color: {_ACCENT_100}; }}"
                f"#stepBtn:pressed {{ background-color: {_ACCENT_200}; }}"
            )
            button.clicked.connect(lambda _checked=False, d=delta: on_change(self.barcode, d))
            buttons.append(button)
        self._qty = QLabel(str(line.quantity))
        self._qty.setAlignment(Qt.AlignCenter)
        self._qty.setFixedWidth(28)
        self._qty.setStyleSheet(f"background: transparent; font-weight: 700; font-size: 17px; color: {p['text_primary']};")
        stepper_layout.addWidget(buttons[0])
        stepper_layout.addWidget(self._qty)
        stepper_layout.addWidget(buttons[1])
        layout.addWidget(stepper)

        self._total = QLabel(format_money(line.line_total, "$"))
        self._total.setFixedWidth(78)
        self._total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._total.setStyleSheet(f"background: transparent; font-weight: 700; font-size: 16px; color: {p['text_primary']};")
        layout.addWidget(self._total)

    @staticmethod
    def _unit_text(line: _CartLine) -> str:
        return tr("pos.sale.each").format(price=format_money(line.unit_price, "$"))

    def update_line(self, line: _CartLine) -> None:
        self._name.setText(line.name)
        self._unit.setText(self._unit_text(line))
        self._unit.update()
        self._name.update()
        count_up(self._qty, str(line.quantity), 220)
        count_up(self._total, format_money(line.line_total, "$"), 320)

    def flash(self) -> None:
        self._flash.go(1.0)
        QTimer.singleShot(140, lambda: self._flash.go(0.0) if _alive(self) else None)

    def grow_in(self) -> None:
        """Fade in while the row's height opens up, so the rows below glide down."""
        if not animations_enabled():
            return
        target = max(1, self.sizeHint().height())
        fade_in(self, 260)
        animation = QPropertyAnimation(self, b"maximumHeight", self)
        animation.setDuration(260)
        animation.setStartValue(0)
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.finished.connect(lambda: self.setMaximumHeight(16777215) if _alive(self) else None)
        self.setMaximumHeight(0)
        self._grow = animation
        animation.start()

    def paintEvent(self, _event) -> None:
        if self._flash.value > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(blend(ORGANIC_PALETTE["surface_raised"], _ACCENT_200, self._flash.value)))
            painter.drawRoundedRect(QRectF(self.rect()), 16, 16)


def _transparent_scroll(scroll: QScrollArea, host: QWidget) -> None:
    """QScrollArea.setWidget turns the host's background fill on: switch it off again."""
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setObjectName("saleScroll")
    scroll.setStyleSheet("#saleScroll { background: transparent; border: none; }")
    scroll.setWidget(host)
    scroll.viewport().setAutoFillBackground(False)
    host.setAutoFillBackground(False)


class NewSalePage(QWidget):
    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._location = location  # this terminal's dealership (UNASSIGNED with no setup identity)
        p = ORGANIC_PALETTE
        self.setObjectName("newSalePage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"#newSalePage {{ background-color: {p['background']}; }}")

        self._all_products: list[Product] = []
        self._cart: dict[str, _CartLine] = {}  # barcode -> _CartLine, insertion order preserved
        self._cards: dict[str, _ProductCard] = {}  # barcode -> the card currently in the grid
        self._rows: dict[str, _CartRow] = {}  # barcode -> its cart row widget
        self._last_total: float | None = None
        self._stagger_pending = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_catalog(), stretch=1)
        layout.addWidget(self._build_cart_panel())

    # --- Catalog (left) ------------------------------------------------

    def _build_catalog(self) -> QWidget:
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 16, 0)
        col.setSpacing(16)

        pill = _SearchPill(tr("pos.sale.search_placeholder"))
        self._search_input = pill.edit
        self._search_input.textChanged.connect(self._render_grid)
        self._search_input.returnPressed.connect(self._on_search_enter)
        pill_wrap = QHBoxLayout()
        pill_wrap.setContentsMargins(8, 4, 8, 4)  # room for the pill's shadow
        pill_wrap.addWidget(pill)
        col.addLayout(pill_wrap)

        scroll = QScrollArea()
        grid_host = QWidget()
        grid_host.setObjectName("saleGridHost")
        self._grid_layout = QGridLayout(grid_host)
        self._grid_layout.setContentsMargins(1, 0, 1, 14)
        self._grid_layout.setSpacing(0)  # the slots carry the 14px gaps themselves
        for column in range(_GRID_COLUMNS):
            self._grid_layout.setColumnStretch(column, 1)
        self._stretch_row = -1
        _transparent_scroll(scroll, grid_host)
        col.addWidget(scroll, stretch=1)

        return container

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stagger_pending:
            QTimer.singleShot(60, self._run_stagger)

    def _on_search_enter(self) -> None:
        """A barcode scanner types the code and presses Enter: the exact match goes straight into the sale."""
        code = self._search_input.text().strip().lower()
        if not code:
            return
        for product in self._all_products:
            if product.is_active and product.barcode.lower() == code and product.stock_quantity > 0:
                self._add_to_cart(product)
                self._search_input.clear()
                return

    def _render_grid(self) -> None:
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if self._stretch_row >= 0:
            self._grid_layout.setRowStretch(self._stretch_row, 0)
        self._cards = {}

        query = self._search_input.text().strip().lower()
        # Deactivated products can't be sold (and the search matches a scanned barcode as well as names).
        visible = [
            product for product in self._all_products
            if product.is_active and (query in product.name.lower() or query in product.barcode.lower())
        ]
        order = {product.barcode: index for index, product in enumerate(self._all_products)}

        for index, product in enumerate(visible):
            card = self._build_product_tile(product, order.get(product.barcode, index))
            self._cards[product.barcode] = card
            self._grid_layout.addWidget(_CardSlot(card), index // _GRID_COLUMNS, index % _GRID_COLUMNS)
        self._stretch_row = (len(visible) + _GRID_COLUMNS - 1) // _GRID_COLUMNS
        self._grid_layout.setRowStretch(self._stretch_row, 1)

        self._stagger_pending = True
        if self.isVisible():
            QTimer.singleShot(0, self._run_stagger)

    def _run_stagger(self) -> None:
        if not self._stagger_pending or not self.isVisible():
            return
        self._stagger_pending = False
        slots = [self._grid_layout.itemAt(i).widget() for i in range(min(self._grid_layout.count(), _STAGGER_LIMIT))]
        self._grid_layout.activate()
        stagger_in([slot for slot in slots if slot is not None], step=32, duration=260, rise=16)

    def _build_product_tile(self, product: Product, tint_index: int = 0) -> _ProductCard:
        p = ORGANIC_PALETTE
        status = stock_status(product)
        out = status == "out"
        page_bg = p["background"]

        def soften(colour: str) -> str:  # "50% opacity" for an out-of-stock card
            return blend(colour, page_bg, 0.5) if out else colour

        bg = soften(p["surface_raised"])
        card = _ProductCard(bg, bg if out else _ACCENT_100, bg if out else _ACCENT_200, dimmed=out)
        card.setMinimumHeight(164)
        card.setEnabled(not out)
        card.clicked.connect(lambda _checked=False, item=product: self._add_to_cart(item))

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        initial = QLabel(upper(product.name[:1]))
        initial.setObjectName("cardInitial")
        initial.setFixedSize(56, 56)
        initial.setAlignment(Qt.AlignCenter)
        initial.setStyleSheet(
            f"#cardInitial {{ background-color: {soften(_TINTS[tint_index % len(_TINTS)])}; border-radius: 28px; "
            f"font-family: {FONT_HEADING_CSS}; font-size: 24px; color: {soften(_NEUTRAL_900)}; }}"
        )
        top_row.addWidget(initial, alignment=Qt.AlignTop)
        top_row.addStretch(1)

        badge_bg, badge_fg = _STATUS_COLORS.get(status, (_NEUTRAL_200, p["text_secondary"]))
        badge_text = tr("pos.sale.tile_out") if out else tr("pos.sale.tile_left").format(n=product.stock_quantity)
        badge = QLabel(badge_text)
        badge.setObjectName("cardBadge")
        badge.setStyleSheet(
            f"#cardBadge {{ background-color: {soften(badge_bg)}; color: {soften(badge_fg)}; border-radius: 14px; "
            f"font-weight: 700; font-size: 13px; padding: 5px 10px; }}"
        )
        top_row.addWidget(badge, alignment=Qt.AlignTop)
        layout.addLayout(top_row)

        name_label = QLabel(product.name)
        name_label.setObjectName("cardName")
        name_label.setWordWrap(True)
        name_label.setStyleSheet(
            f"#cardName {{ background: transparent; font-weight: 700; font-size: 16px; color: {soften(p['text_primary'])}; }}"
        )
        detail_label = QLabel(product.barcode)
        detail_label.setObjectName("cardDetail")
        detail_label.setStyleSheet(
            f"#cardDetail {{ background: transparent; font-size: 14px; color: {soften(p['text_secondary'])}; }}"
        )
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.addWidget(name_label)
        text_col.addWidget(detail_label)
        layout.addLayout(text_col)
        layout.addStretch(1)

        price_label = QLabel(format_money(product.price, "$"))
        price_label.setObjectName("cardPrice")
        price_label.setStyleSheet(
            f"#cardPrice {{ background: transparent; font-weight: 700; font-size: 19px; color: {soften(p['text_primary'])}; }}"
        )
        layout.addWidget(price_label)

        for label in (initial, badge, name_label, detail_label, price_label):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        return card

    # --- Cart (right) --------------------------------------------------

    def _build_cart_panel(self) -> QWidget:
        p = ORGANIC_PALETTE
        panel = _Surface(p["surface_raised"], 28, top_left_only=True)
        panel.setFixedWidth(392)
        soft_shadow(panel, "md")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 22, 16, 12)
        title = QLabel(tr("pos.sale.current_sale"))
        title.setObjectName("saleTitle")
        title.setStyleSheet(
            f"#saleTitle {{ background: transparent; font-family: {FONT_HEADING_CSS}; font-size: 28px; color: {p['text_primary']}; }}"
        )
        header.addWidget(title)
        header.addStretch(1)
        clear_button = QPushButton(tr("pos.sale.clear"))
        clear_button.setObjectName("saleClear")
        clear_button.setCursor(Qt.PointingHandCursor)
        clear_button.setFocusPolicy(Qt.TabFocus)
        clear_button.setStyleSheet(
            f"#saleClear {{ border: none; background: none; font-weight: 600; font-size: 15px; "
            f"color: {_ACCENT_700}; padding: 8px; }}"
            f"#saleClear:hover {{ color: {p['accent']}; }}"
        )
        clear_button.clicked.connect(self._on_clear_clicked)
        header.addWidget(clear_button)
        layout.addLayout(header)

        scroll = QScrollArea()
        cart_host = QWidget()
        cart_host.setObjectName("saleCartHost")
        self._cart_rows_layout = QVBoxLayout(cart_host)
        self._cart_rows_layout.setContentsMargins(16, 0, 16, 0)
        self._cart_rows_layout.setSpacing(0)

        # Empty state: neutral-200 card, centred text (a persistent widget shown / hidden with the cart).
        self._empty_wrap = QWidget()
        wrap_layout = QVBoxLayout(self._empty_wrap)
        wrap_layout.setContentsMargins(8, 40, 8, 40)
        empty_card = _Surface(_NEUTRAL_200, 28)
        empty_layout = QVBoxLayout(empty_card)
        empty_layout.setContentsMargins(24, 32, 24, 32)
        empty_label = QLabel(tr("pos.sale.tap_to_add"))
        empty_label.setObjectName("saleEmpty")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setWordWrap(True)
        empty_label.setStyleSheet(
            f"#saleEmpty {{ background: transparent; color: {p['text_secondary']}; font-size: 16px; }}"
        )
        empty_layout.addWidget(empty_label)
        wrap_layout.addWidget(empty_card)
        self._cart_rows_layout.addWidget(self._empty_wrap)
        self._cart_rows_layout.addStretch(1)
        _transparent_scroll(scroll, cart_host)
        layout.addWidget(scroll, stretch=1)

        # Why the cart changed under the cashier (a price moved, an item was withdrawn, stock ran short).
        self._cart_notice = QLabel()
        self._cart_notice.setObjectName("saleNotice")
        self._cart_notice.setWordWrap(True)
        self._cart_notice.setStyleSheet(
            "#saleNotice { background-color: #fff6d6; color: #3a2a05; border: 1px solid #f4b400; "
            "border-radius: 12px; padding: 8px 12px; margin: 0 16px 8px 16px; font-size: 14px; }"
        )
        self._cart_notice.hide()
        layout.addWidget(self._cart_notice)

        footer = _Surface(_NEUTRAL_200)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(24, 18, 24, 24)
        footer_layout.setSpacing(14)

        vat_row = QHBoxLayout()
        self._item_count_label = QLabel(self._items_text(0))
        self._item_count_label.setObjectName("saleItems")
        self._vat_label = QLabel(self._vat_text(0.0))
        self._vat_label.setObjectName("saleVat")
        for label in (self._item_count_label, self._vat_label):
            label.setStyleSheet(
                f"#{label.objectName()} {{ background: transparent; font-size: 15px; color: {p['text_secondary']}; }}"
            )
        vat_row.addWidget(self._item_count_label)
        vat_row.addStretch(1)
        vat_row.addWidget(self._vat_label)
        footer_layout.addLayout(vat_row)

        total_row = QHBoxLayout()
        total_caption = QLabel(tr("common.total"))
        total_caption.setObjectName("saleTotalCaption")
        total_caption.setStyleSheet(
            f"#saleTotalCaption {{ background: transparent; font-size: 18px; font-weight: 600; color: {p['text_primary']}; }}"
        )
        self._total_label = QLabel(format_money(0, "$"))
        self._total_label.setObjectName("saleTotal")
        self._total_pop = Level(self, self._style_total, 200)
        self._style_total(0.0)
        total_row.addWidget(total_caption)
        total_row.addStretch(1)
        total_row.addWidget(self._total_label)
        footer_layout.addLayout(total_row)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(10)
        self._card_button = PillButton(tr("pos.sale.card"), p["accent"], _ACCENT_600, _ACCENT_700, "#ffffff", 68, 19)
        self._card_button.clicked.connect(lambda: self._checkout("Card"))
        self._cash_button = PillButton(
            tr("pos.sale.cash"), p["text_primary"], _NEUTRAL_800, _ACCENT_700, p["background"], 68, 19
        )
        self._cash_button.clicked.connect(lambda: self._checkout("Cash"))
        self._card_button.setEnabled(False)
        self._cash_button.setEnabled(False)
        buttons_row.addWidget(self._card_button)
        buttons_row.addWidget(self._cash_button)
        footer_layout.addLayout(buttons_row)

        layout.addWidget(footer)
        return panel

    def _style_total(self, level: float) -> None:
        colour = blend(ORGANIC_PALETTE["text_primary"], ORGANIC_PALETTE["accent"], level)
        self._total_label.setStyleSheet(
            f"#saleTotal {{ background: transparent; font-family: {FONT_HEADING_CSS}; font-size: 40px; color: {colour}; }}"
        )

    @staticmethod
    def _items_text(count: int) -> str:
        return tr("pos.sale.items_vat").format(items=plural("pos.sale.items", count))

    @staticmethod
    def _vat_text(total: float) -> str:
        vat = round(total - total / (1 + _VAT_RATE), 2)
        return tr("pos.sale.vat_amount").format(amount=format_money(vat, "$"))

    def _render_cart(self) -> None:
        # Drop rows whose line is gone, add rows for new lines, update the rest in place.
        for barcode in [code for code in self._rows if code not in self._cart]:
            row = self._rows.pop(barcode)
            self._cart_rows_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for index, line in enumerate(self._cart.values()):
            row = self._rows.get(line.barcode)
            if row is None:
                row = _CartRow(line, self._change_quantity)
                self._rows[line.barcode] = row
                self._cart_rows_layout.insertWidget(1 + index, row)  # slot 0 is the empty-state card
                row.show()
                row.grow_in()
            else:
                row.update_line(line)

        was_shown = not self._empty_wrap.isHidden()
        self._empty_wrap.setVisible(not self._cart)
        if not self._cart and not was_shown:
            fade_in(self._empty_wrap, 240)

        item_count = sum(line.quantity for line in self._cart.values())
        total = round(sum(line.line_total for line in self._cart.values()), 2)
        self._item_count_label.setText(self._items_text(item_count))
        self._vat_label.setText(self._vat_text(total))
        count_up(self._total_label, format_money(total, "$"), 380)
        if self._last_total is not None and total != self._last_total:
            self._total_pop.go(1.0)
            QTimer.singleShot(200, lambda: self._total_pop.go(0.0) if _alive(self) else None)
        self._last_total = total
        for button in (self._card_button, self._cash_button):
            button.setEnabled(bool(self._cart))

    # --- Cart mutation ---------------------------------------------------

    def _add_to_cart(self, product: Product) -> None:
        existing = self._cart.get(product.barcode)
        if existing is not None:
            existing.quantity = min(existing.quantity + 1, product.stock_quantity)
        else:
            self._cart[product.barcode] = _CartLine(
                barcode=product.barcode,
                name=product.name,
                unit_price=product.price,
                quantity=1,
                max_quantity=product.stock_quantity,
            )
        self._render_cart()
        card = self._cards.get(product.barcode)
        if card is not None and _alive(card):
            card.pulse()
        row = self._rows.get(product.barcode)
        if row is not None:
            row.flash()

    def _change_quantity(self, barcode: str, delta: int) -> None:
        line = self._cart.get(barcode)
        if line is None:
            return
        new_quantity = min(line.quantity + delta, line.max_quantity)
        if new_quantity <= 0:
            del self._cart[barcode]
        else:
            line.quantity = new_quantity
        self._render_cart()

    def clear_cart(self) -> None:
        """Empty the current sale (a new cashier takes over the till)."""
        self._clear_cart()

    def _clear_cart(self) -> None:
        self._cart.clear()
        self._render_cart()

    def _on_clear_clicked(self) -> None:
        had_items = bool(self._cart)
        self._clear_cart()
        if had_items:
            self._notify(tr("pos.sale.cleared"))

    def _notify(self, text: str) -> None:
        """The dark-pill confirmation: the main window's own if it has one, else a toast on this page's window."""
        win = self.window()
        notify = getattr(win, "notify", None)
        if callable(notify):
            notify(text)
        else:
            toast(self, text, 2600, style=toast_style())

    # --- Data + checkout -------------------------------------------------

    def has_items(self) -> bool:
        return bool(self._cart)

    def reload(self) -> None:
        try:
            self._all_products = stock_repository.products_at(self._location)
        except DATABASE_ERRORS:
            self._all_products = []
        issues = self._refresh_cart()  # prices / availability may have changed since the lines were added
        self._show_notice(issues)
        self._render_grid()
        self._render_cart()

    # --- keeping the cart true to the database -------------------------------

    def _cart_triples(self) -> list[tuple[str, float, int]]:
        return [(line.barcode, line.unit_price, line.quantity) for line in self._cart.values()]

    def _refresh_cart(self) -> list:
        """Re-read every cart line from the database and bring it in line:
        a changed price is taken over, a withdrawn / deactivated product is
        removed, a quantity above what the shelf holds is cut back.
        Returns the CartIssues found (empty: the cart was already right)."""
        if not self._cart:
            return []
        try:
            issues = product_repository.check_cart(self._location, self._cart_triples())
        except DATABASE_ERRORS:
            return []
        self._apply_issues(issues)
        return issues

    def _apply_issues(self, issues) -> None:
        for issue in issues:
            line = self._cart.get(issue.barcode)
            if line is None:
                continue
            if issue.kind == "unavailable":
                del self._cart[issue.barcode]
            elif issue.kind == "price":
                line.unit_price = issue.current_price
                line.max_quantity = issue.available
                line.quantity = min(line.quantity, issue.available)
            elif issue.kind == "stock":
                line.max_quantity = issue.available
                line.quantity = min(line.quantity, issue.available)
            if issue.barcode in self._cart and self._cart[issue.barcode].quantity <= 0:
                del self._cart[issue.barcode]
        # lines that were fine keep their limit current too
        for product in self._all_products:
            line = self._cart.get(product.barcode)
            if line is not None:
                line.max_quantity = product.stock_quantity

    def _show_notice(self, issues) -> None:
        if issues:
            self._cart_notice.setText(" ".join(issue.message for issue in issues)
                                      + " " + tr_or("pos.cart_refreshed", "The cart has been updated - check it before charging."))
            self._cart_notice.show()
        else:
            self._cart_notice.hide()

    def cart_notice(self) -> str:
        """The visible cart-changed notice ('' when none) - for tests."""
        return self._cart_notice.text() if not self._cart_notice.isHidden() else ""

    def _warn_sign_in_again(self) -> None:
        QMessageBox.warning(
            self, tr_or("pos.sign_in_again_title", "Sign in again"),
            str(SessionInvalidError()) + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
        )

    def _cart_changed_by_database(self, exc: Exception) -> None:
        """finalize_transaction() refused the sale because the cart no longer
        matches the database. Re-read it (new prices, withdrawn lines
        removed, quantities cut back), show the 'cart changed' notice and
        tell the cashier nothing was charged."""
        self.reload()  # refreshes the cart from the database and shows what changed
        if not self.cart_notice():  # the refresh found nothing to add: use the database's own words
            self._cart_notice.setText(str(exc))
            self._cart_notice.show()
        QMessageBox.warning(
            self,
            tr_or("pos.cart_changed_title", "Cart changed"),
            str(exc) + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
        )

    def _checkout(self, payment_label: str) -> None:
        if not self._cart:
            QMessageBox.information(self, tr("pos.cart_empty"), tr("pos.sale.empty_body"))
            return

        # Re-read prices and availability NOW: the till may have been open for a while, and an admin
        # can change a price (or withdraw a product) at any time. Refuse, refresh, and let the cashier look again.
        try:
            issues = product_repository.check_cart(self._location, self._cart_triples())
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("pos.sale.failed_title"), str(exc))
            return
        if issues:
            self._apply_issues(issues)
            self.reload()  # fresh grid + cart (clears the notice, so show it after)
            self._show_notice(issues)
            QMessageBox.warning(
                self,
                tr_or("pos.cart_changed_title", "Cart changed"),
                " ".join(issue.message for issue in issues)
                + "\n\n" + tr_or("pos.cart_changed_body", "Nothing was charged. Check the updated cart and try again."),
            )
            return

        pending = Transaction(
            payment_method=payment_label.lower() if payment_label.lower() in PAYMENT_METHODS else None,
            items=[
                LineItem(
                    product_barcode=line.barcode,
                    product_name_at_sale=line.name,
                    unit_price_at_sale=line.unit_price,
                    quantity=line.quantity,
                )
                for line in self._cart.values()
            ]
        )

        # Friendly pre-checks. finalize_transaction() repeats every one of them
        # inside its own transaction (that is the authoritative check - these
        # only spare the cashier a round trip), so nothing here is load-bearing.
        session = current_session.get()
        if session is not None and not account_repository.is_session_valid(session):
            self._warn_sign_in_again()
            return
        if self._location.kind == "dealership":
            problem = till_dealership_problem(self._location.code)
            if problem:
                QMessageBox.warning(self, tr_or("pos.till_off_title", "Till switched off"), problem)
                return

        try:
            finalized = complete_sale(pending, self._location, current_session.actor())
        except (PriceChangedError, ProductNotFoundError, InsufficientStockError) as exc:
            # The database found the cart out of date at the moment of charging
            # (a price moved, a product was withdrawn/deactivated, stock ran
            # out): nothing was charged. Bring the cart in line and say so.
            self._cart_changed_by_database(exc)
            return
        except SessionInvalidError:
            self._warn_sign_in_again()
            return
        except DealershipInactiveError as exc:
            QMessageBox.warning(self, tr_or("pos.till_off_title", "Till switched off"), str(exc))
            return
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("pos.sale.failed_title"), str(exc))
            return

        self._cart.clear()
        self._cart_notice.hide()
        self.reload()
        body = tr("pos.sale.complete_body").format(
            payment=tr(_PAYMENT_KEYS[payment_label]) if payment_label in _PAYMENT_KEYS else payment_label,
            id=finalized.id,
            total=format_money(finalized.total, "$"),
        )
        self._notify(body)
        QMessageBox.information(self, tr("pos.sale.complete_title"), body)
