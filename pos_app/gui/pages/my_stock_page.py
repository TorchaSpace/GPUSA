"""My Local Stock screen - recreates the mockup's stock list: filter
pills (All/Low/Out) with live counts, then one row per product (on-hand
qty, a fill bar, and a status badge).

Fully real - database.stock_repository.products_at(this dealership), no placeholder
data. The one thing invented rather than read from the database is the
fill bar's percentage: the mockup implies some per-product "capacity",
which Product (see shared.models) has no field for, so the bar here is
a fixed heuristic (on-hand relative to 2x the reorder point) purely for
a visual sense of "how full", not a real capacity metric - see
_fill_pct()'s docstring.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from database import stock_repository
from database.exceptions import DATABASE_ERRORS
from pos_app.gui.product_status import stock_status
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.i18n import tr
from shared.models import UNASSIGNED, Product, StockLocation
from shared.textcase import upper
from shared.warehousing import tr_or

_FILTER_KEYS = {"All": "pos.stock.filter_all", "Low": "pos.stock.filter_low", "Out": "pos.stock.filter_out"}

_STATUS_META = {
    # status -> (dot color, badge bg, badge fg, badge text KEY - tr() at render time, qty color, bar color)
    "out": ("#d8412f", "#d8412f", "white", "pos.stock.status_out", "#d8412f", "#d8412f"),
    "low": ("#f2c230", "#f2c230", "#3a2a05", "pos.stock.status_low", "#c67139", "#f2c230"),
    "ok": ("#7a8a5e", "#e1eecc", "#3d472b", "pos.stock.status_ok", None, "#7a8a5e"),
}


def _fill_pct(product: Product) -> int:
    """Heuristic-only visual fill: on-hand relative to 2x the reorder
    point. Product has no real capacity field - see module docstring."""
    denominator = max(product.critical_stock_level * 2, 1)
    return max(0, min(100, round(product.stock_quantity / denominator * 100)))


class MyStockPage(QWidget):
    def __init__(self, location: StockLocation = UNASSIGNED, parent: QWidget | None = None):
        super().__init__(parent)
        self._location = location  # this terminal's dealership shelf
        p = ORGANIC_PALETTE
        self.setStyleSheet(f"background-color: {p['background']};")

        self._all_products: list[Product] = []
        self._active_filter = "All"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 12, 28, 0)
        outer.setSpacing(18)

        outer.addWidget(self._build_header())

        self._columns_header = self._build_columns_header()
        outer.addWidget(self._columns_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        rows_host = QWidget()
        self._rows_layout = QVBoxLayout(rows_host)
        self._rows_layout.setSpacing(8)
        self._rows_layout.addStretch(1)
        scroll.setWidget(rows_host)
        outer.addWidget(scroll, stretch=1)

        self.reload()

    def _build_header(self) -> QWidget:
        p = ORGANIC_PALETTE
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        self._subtitle_label = QLabel()
        self._subtitle_label.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        titles.addWidget(self._subtitle_label)
        title = QLabel(tr("pos.stock.title"))
        title.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-weight: 400; font-size: 40px; color: {p['text_primary']};")
        titles.addWidget(title)
        titles_widget = QWidget()
        titles_widget.setLayout(titles)
        row.addWidget(titles_widget)
        row.addStretch(1)

        self._filter_pill = QWidget()
        self._filter_pill.setStyleSheet(f"background-color: {p['surface']}; border-radius: 999px;")
        pill_layout = QHBoxLayout(self._filter_pill)
        pill_layout.setContentsMargins(6, 6, 6, 6)
        pill_layout.setSpacing(6)
        self._filter_buttons: dict[str, QPushButton] = {}
        for key in ("All", "Low", "Out"):
            button = QPushButton()
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(52)
            button.clicked.connect(lambda checked, k=key: self._set_filter(k))
            pill_layout.addWidget(button)
            self._filter_buttons[key] = button
        self._filter_buttons["All"].setChecked(True)
        row.addWidget(self._filter_pill)

        container = QWidget()
        container.setLayout(row)
        return container

    def _build_columns_header(self) -> QWidget:
        p = ORGANIC_PALETTE
        row = QHBoxLayout()
        row.setContentsMargins(20, 0, 20, 0)
        for column, stretch in (("", 0), ("product", 3), ("sku", 1), ("on_hand", 1), ("level", 2), ("status", 2)):
            label = QLabel(upper(tr(f"pos.stock.col_{column}")) if column else "")
            label.setStyleSheet(f"font-size: 13px; font-weight: 700; letter-spacing: 1px; color: {p['text_secondary']};")
            if column == "on_hand":
                label.setAlignment(Qt.AlignRight)
            row.addWidget(label, stretch=stretch if stretch else 0, alignment=Qt.Alignment())
        container = QWidget()
        container.setLayout(row)
        return container

    def _set_filter(self, key: str) -> None:
        self._active_filter = key
        for filter_key, button in self._filter_buttons.items():
            button.setChecked(filter_key == key)
        self._render_rows()

    def reload(self) -> None:
        try:
            here = stock_repository.products_at(self._location)
        except DATABASE_ERRORS:
            here = []
        # "My stock" is what this shelf carries: products it holds or has held. The rest of the
        # catalogue isn't "out of stock" here - it was never stocked - and would swamp the Out count.
        self._all_products = [p for p in here if p.stocked_here or p.stock_quantity > 0]
        self._subtitle_label.setText(tr("pos.stock.counted").format(time=datetime.now().strftime("%H:%M")))
        self._render_filter_labels()
        self._render_rows()

    def _render_filter_labels(self) -> None:
        p = ORGANIC_PALETTE
        counts = {"All": len(self._all_products), "Low": 0, "Out": 0}
        for product in self._all_products:
            status = stock_status(product)
            if status == "low":
                counts["Low"] += 1
            elif status == "out":
                counts["Out"] += 1

        dots = {"All": p["accent_2"], "Low": "#f2c230", "Out": "#d8412f"}
        for key, button in self._filter_buttons.items():
            button.setText(f"  {tr(_FILTER_KEYS[key])}  {counts[key]}")
            button.setStyleSheet(
                f"""
                QPushButton {{
                    border: none;
                    border-radius: 26px;
                    padding: 0 20px;
                    font-size: 16px;
                    font-weight: 700;
                    color: {p['text_primary']};
                    background: transparent;
                }}
                QPushButton:checked {{
                    background-color: {p['background']};
                }}
                """
            )

    def _render_rows(self) -> None:
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._active_filter == "All":
            visible = self._all_products
        else:
            wanted = "low" if self._active_filter == "Low" else "out"
            visible = [product for product in self._all_products if stock_status(product) == wanted]

        for index, product in enumerate(visible):
            self._rows_layout.insertWidget(index, self._build_row(product))

    def _build_row(self, product: Product) -> QWidget:
        p = ORGANIC_PALETTE
        status = stock_status(product)
        dot_color, badge_bg, badge_fg, badge_key, qty_color, bar_color = _STATUS_META[status]

        row = QWidget()
        row.setStyleSheet(f"background-color: {p['surface_raised']}; border-radius: 26px;")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(16)

        avatar = QLabel(upper(product.name[:1]))
        avatar.setFixedSize(52, 52)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(f"background-color: {p['surface']}; border-radius: 26px; font-family: {FONT_HEADING_CSS}; font-size: 22px;")
        layout.addWidget(avatar)

        names = QVBoxLayout()
        names.setSpacing(0)
        name_label = QLabel(product.name if product.is_active else f"{product.name} ({tr_or('admin.inactive_badge', 'inactive')})")
        name_label.setStyleSheet(f"font-weight: 700; font-size: 18px; color: {p['text_primary'] if product.is_active else p['text_secondary']};")
        reorder_label = QLabel(tr("pos.stock.reorder_at").format(n=product.critical_stock_level) if product.critical_stock_level else tr("pos.stock.no_reorder"))
        reorder_label.setStyleSheet(f"font-size: 14px; color: {p['text_secondary']};")
        names.addWidget(name_label)
        names.addWidget(reorder_label)
        names_widget = QWidget()
        names_widget.setLayout(names)
        layout.addWidget(names_widget, stretch=3)

        sku_label = QLabel(product.barcode)
        sku_label.setStyleSheet(f"font-size: 15px; color: {p['text_secondary']};")
        layout.addWidget(sku_label, stretch=1)

        qty_label = QLabel(str(product.stock_quantity))
        qty_label.setAlignment(Qt.AlignRight)
        qty_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 28px; color: {qty_color or p['text_primary']};"
        )
        layout.addWidget(qty_label, stretch=1)

        bar_track = QWidget()
        bar_track.setFixedHeight(12)
        bar_track.setStyleSheet(f"background-color: {p['border']}; border-radius: 6px;")
        bar_fill = QWidget(bar_track)
        pct = _fill_pct(product)
        bar_fill.setStyleSheet(f"background-color: {bar_color}; border-radius: 6px;")
        bar_fill.setGeometry(0, 0, 0, 12)
        bar_track.resizeEvent = lambda event, track=bar_track, fill=bar_fill, pct=pct: fill.setGeometry(
            0, 0, int(track.width() * pct / 100), 12
        )
        layout.addWidget(bar_track, stretch=2)

        badge = QLabel(tr(badge_key))
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_fg}; border-radius: 999px; "
            f"font-weight: 800; font-size: 15px; padding: 9px 16px;"
        )
        badge_wrap = QHBoxLayout()
        badge_wrap.addStretch(1)
        badge_wrap.addWidget(badge)
        badge_widget = QWidget()
        badge_widget.setLayout(badge_wrap)
        layout.addWidget(badge_widget, stretch=2)

        return row
