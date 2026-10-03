"""Set-safe-price-range popup - same RefreshablePopup + QFormLayout shape
as dealership_form_popup.py. Product (fixed once editing), minimum and
maximum unit price, and an optional default supplier (pre-fills the
depot's purchase form, like the mockup's per-SKU supplier)."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit

from shared.i18n import tr
from shared.gui_kit.popup_window import RefreshablePopup
from shared.models import PriceRange, Product

_MAX_PRICE = 1_000_000_000


def _money_spin() -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setDecimals(2)
    spin.setRange(0, _MAX_PRICE)
    spin.setGroupSeparatorShown(True)
    return spin


class PriceRangeFormPopup(RefreshablePopup):
    """Emits QDialog's `accepted`; the host reads result_range()."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("admin.purchase.p_title"))
        self._product_input = QComboBox()
        self._min_input = _money_spin()
        self._max_input = _money_spin()
        self._supplier_input = QLineEdit()
        self._supplier_input.setPlaceholderText(tr("admin.purchase.p_optional"))
        self._error = QLabel()
        self._error.setWordWrap(True)
        self._error.hide()

        form = QFormLayout()
        form.addRow(tr("admin.purchase.p_product"), self._product_input)
        form.addRow(tr("admin.purchase.p_min"), self._min_input)
        form.addRow(tr("admin.purchase.p_max"), self._max_input)
        form.addRow(tr("admin.purchase.p_supplier"), self._supplier_input)
        hint = QLabel(tr("admin.purchase.p_note"))
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def refresh_content(self, products: list[Product] = (), price_range: PriceRange | None = None) -> None:
        self._error.hide()
        self._product_input.clear()
        for product in products:
            self._product_input.addItem(f"{product.barcode} · {product.name}", product.barcode)
        if price_range is not None:
            index = self._product_input.findData(price_range.product_barcode)
            if index >= 0:
                self._product_input.setCurrentIndex(index)
        self._product_input.setEnabled(price_range is None)
        self._min_input.setValue(price_range.min_unit_price if price_range else 0)
        self._max_input.setValue(price_range.max_unit_price if price_range else 0)
        self._supplier_input.setText((price_range.default_supplier or "") if price_range else "")
        self.setWindowTitle(tr("admin.purchase.p_edit") if price_range else tr("admin.purchase.p_set"))

    def _validate_and_accept(self) -> None:
        if self._product_input.currentData() is None:
            self._show_error(tr("admin.purchase.p_err_product"))
            return
        if self._min_input.value() > self._max_input.value():
            self._show_error(tr("admin.purchase.p_err_minmax"))
            return
        if self._max_input.value() <= 0:
            self._show_error(tr("admin.purchase.p_err_max"))
            return
        self.accept()

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.show()

    def result_range(self) -> PriceRange:
        return PriceRange(
            product_barcode=self._product_input.currentData(),
            min_unit_price=round(self._min_input.value(), 2),
            max_unit_price=round(self._max_input.value(), 2),
            default_supplier=self._supplier_input.text().strip() or None,
        )
