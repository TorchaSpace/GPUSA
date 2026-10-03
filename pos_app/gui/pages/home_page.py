"""Home screen - recreates the mockup's Dashboard section: a greeting
plus 3 large tap tiles (New Sale / Receive Inventory / My Local Stock).

Real vs. placeholder, explicitly:
- "X out"/"Y low" badge on the My Local Stock tile: REAL, computed from
  database.stock_repository - this dealership's shelf (same stock_status() classification the
  My Local Stock screen itself uses - see task #59).
- "N arriving" badge on the Receive Inventory tile: REAL - shipments
  still on their way to this terminal's dealership
  (shipment_repository.count_incoming(); every dealership's when the
  terminal has no setup file).
- The mockup's per-minute greeting ("Good afternoon, Selin") and date
  line are real (wall-clock), not mock data - there's no reason to fake
  the clock.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from database import shipment_repository, stock_repository
from database.exceptions import DataAccessError
from pos_app.gui.product_status import stock_status
from pos_app.theme import FONT_HEADING, ORGANIC_PALETTE
from shared.models import UNASSIGNED, StockLocation


class _Tile(QPushButton):
    def __init__(self, bg: str, hover_bg: str, fg: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(260)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: 40px;
                text-align: left;
                padding: 0;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
            }}
            """
        )


class HomePage(QWidget):
    tile_clicked = Signal(str)  # "sale" | "receive" | "stock"

    def __init__(self, cashier_first_name: str, dealership_code: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._dealership_code = dealership_code
        self._location = StockLocation.dealership(dealership_code) if dealership_code else UNASSIGNED
        p = ORGANIC_PALETTE
        self.setStyleSheet(f"background-color: {p['background']};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 32)
        outer.setSpacing(28)

        greeting_block = QVBoxLayout()
        greeting_block.setSpacing(4)
        self._date_label = QLabel()
        self._date_label.setStyleSheet(f"font-size: 16px; color: {p['text_secondary']};")
        greeting_block.addWidget(self._date_label)
        self._greeting_label = QLabel()
        self._greeting_label.setStyleSheet(
            f"font-family: '{FONT_HEADING}'; font-weight: 400; font-size: 48px; color: {p['text_primary']};"
        )
        greeting_block.addWidget(self._greeting_label)
        outer.addLayout(greeting_block)

        self._set_greeting(cashier_first_name)

        tiles = QGridLayout()
        tiles.setSpacing(20)
        tiles.setColumnStretch(0, 5)
        tiles.setColumnStretch(1, 4)
        tiles.setColumnStretch(2, 4)

        self._sale_tile = self._build_sale_tile()
        self._receive_tile = self._build_receive_tile()
        self._stock_tile = self._build_stock_tile()
        tiles.addWidget(self._sale_tile, 0, 0)
        tiles.addWidget(self._receive_tile, 0, 1)
        tiles.addWidget(self._stock_tile, 0, 2)

        outer.addLayout(tiles, stretch=1)

        self.reload_badges()

    def set_cashier(self, cashier_first_name: str) -> None:
        """A new cashier signed in (Switch cashier)."""
        self._set_greeting(cashier_first_name)

    def _set_greeting(self, cashier_first_name: str) -> None:
        now = datetime.now()
        self._date_label.setText(now.strftime("%A, %d %B"))
        hour = now.hour
        part_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        self._greeting_label.setText(f"Good {part_of_day}, {cashier_first_name}")

    def _build_sale_tile(self) -> QWidget:
        p = ORGANIC_PALETTE
        tile = _Tile(bg=p["accent"], hover_bg="#d67f48", fg="white")
        tile.clicked.connect(lambda: self.tile_clicked.emit("sale"))

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(16)

        icon = QLabel("🛒")
        icon.setFixedSize(88, 88)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background-color: white; border-radius: 44px; font-size: 34px;")
        layout.addWidget(icon)
        layout.addStretch(1)

        title = QLabel("New Sale")
        title.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 52px; color: white;")
        layout.addWidget(title)
        subtitle = QLabel("Scan or tap products, take payment")
        subtitle.setStyleSheet("font-size: 18px; color: #fff2eb;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        return tile

    def _build_receive_tile(self) -> QWidget:
        p = ORGANIC_PALETTE
        tile = _Tile(bg=p["accent_2"], hover_bg="#728157", fg="white")
        tile.clicked.connect(lambda: self.tile_clicked.emit("receive"))

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(16)

        top_row = QVBoxLayout()
        icon = QLabel("📦")
        icon.setFixedSize(88, 88)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background-color: white; border-radius: 44px; font-size: 34px;")
        top_row.addWidget(icon)

        self._receive_badge = QLabel()
        self._receive_badge.setAlignment(Qt.AlignRight)
        self._receive_badge.setStyleSheet(
            "background-color: white; color: #3d472b; border-radius: 999px; "
            "font-weight: 700; font-size: 14px; padding: 6px 12px;"
        )
        top_row.addWidget(self._receive_badge, alignment=Qt.AlignRight)
        layout.addLayout(top_row)
        layout.addStretch(1)

        title = QLabel("Receive Inventory")
        title.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 40px; color: white;")
        layout.addWidget(title)
        subtitle = QLabel("Check in warehouse shipments")
        subtitle.setStyleSheet("font-size: 16px; color: #f0fae1;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        return tile

    def _build_stock_tile(self) -> QWidget:
        p = ORGANIC_PALETTE
        tile = _Tile(bg=p["surface_raised"], hover_bg=p["surface"], fg=p["text_primary"])
        tile.clicked.connect(lambda: self.tile_clicked.emit("stock"))

        layout = QVBoxLayout(tile)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(16)

        top_row = QVBoxLayout()
        top_row.setSpacing(8)
        icon = QLabel("📋")
        icon.setFixedSize(88, 88)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"background-color: {p['surface']}; border-radius: 44px; font-size: 34px;")
        top_row.addWidget(icon)

        self._out_badge = QLabel()
        self._out_badge.setAlignment(Qt.AlignRight)
        self._out_badge.setStyleSheet(
            "background-color: #d8412f; color: white; border-radius: 999px; "
            "font-weight: 700; font-size: 14px; padding: 6px 12px;"
        )
        self._low_badge = QLabel()
        self._low_badge.setAlignment(Qt.AlignRight)
        self._low_badge.setStyleSheet(
            "background-color: #f2c230; color: #3a2a05; border-radius: 999px; "
            "font-weight: 700; font-size: 14px; padding: 6px 12px;"
        )
        top_row.addWidget(self._out_badge, alignment=Qt.AlignRight)
        top_row.addWidget(self._low_badge, alignment=Qt.AlignRight)
        layout.addLayout(top_row)
        layout.addStretch(1)

        title = QLabel("My Local Stock")
        title.setStyleSheet(f"font-family: '{FONT_HEADING}'; font-size: 40px; color: {p['text_primary']};")
        layout.addWidget(title)
        subtitle = QLabel("See what's on the shelf and what's missing")
        subtitle.setStyleSheet(f"font-size: 16px; color: {p['text_secondary']};")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        return tile

    def reload_badges(self) -> None:
        try:
            products = stock_repository.products_at(self._location)
        except DataAccessError:
            products = []
        out_count = sum(1 for product in products if stock_status(product) == "out")
        low_count = sum(1 for product in products if stock_status(product) == "low")
        self._out_badge.setText(f"{out_count} out")
        self._low_badge.setText(f"{low_count} low")
        try:
            arriving = shipment_repository.count_incoming(self._dealership_code)
        except DataAccessError:
            arriving = 0
        self._receive_badge.setText(f"{arriving} arriving")
