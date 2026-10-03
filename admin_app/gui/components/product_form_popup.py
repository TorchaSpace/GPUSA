"""Add/edit-product popup: a RefreshablePopup wrapping a simple form.

ProductManagementTab keeps ONE instance of this alive and reuses it via
open_or_refresh() for both "Add Product" and "Edit Product" -
refresh_content() rebuilds the form fields from whatever Product (or
None, for Add) it's called with, rather than a new popup being
constructed each time. See shared/gui_kit/popup_window.py's docstring
for why that matters.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from shared.gui_kit.popup_window import RefreshablePopup
from shared.i18n import tr
from shared.models import Product
from shared.warehousing import normalise_barcode

_MAX_PRICE = 1_000_000
_MAX_QUANTITY = 1_000_000


class ProductFormPopup(RefreshablePopup):
    """Emits QDialog's own `accepted` signal when Save is pressed - the
    caller (ProductManagementTab) connects to that and reads the result
    back via result_product() / is_editing() rather than this class
    reaching into the repository itself; that keeps this popup a pure
    form, with no database import of its own.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._editing_barcode: str | None = None  # None => Add mode

        self._barcode_input = QLineEdit()
        self._name_input = QLineEdit()

        self._price_input = QDoubleSpinBox()
        self._price_input.setRange(0, _MAX_PRICE)
        self._price_input.setDecimals(2)

        self._stock_input = QSpinBox()
        self._stock_input.setRange(0, _MAX_QUANTITY)

        self._critical_input = QSpinBox()
        self._critical_input.setRange(0, _MAX_QUANTITY)

        # Shown only for a deactivated product being edited (see refresh_content).
        self._inactive_label = QLabel(tr("admin.form_inactive_note"))
        self._inactive_label.setVisible(False)
        # Why Save did nothing: filled by accept() when validation fails.
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: #c0392b;")
        self._error_label.setVisible(False)
        self._editing_active = True

        form = QFormLayout()
        form.addRow(self._inactive_label)
        form.addRow(tr("admin.form_barcode"), self._barcode_input)
        form.addRow(tr("admin.form_name"), self._name_input)
        form.addRow(tr("admin.form_price"), self._price_input)
        form.addRow(tr("admin.form_stock"), self._stock_input)
        form.addRow(tr("admin.form_critical_level"), self._critical_input)
        form.addRow(self._error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setLayout(form)

    def refresh_content(self, product: Product | None = None, draft: Product | None = None) -> None:
        """`product`: the one being edited (None = Add). `draft`: values to
        show instead of the product's own - what the user had typed when a
        save was refused, so they can fix it rather than start over."""
        self._editing_barcode = product.barcode if product is not None else None
        self._editing_active = product.is_active if product is not None else True
        self._inactive_label.setVisible(product is not None and not product.is_active)
        self._error_label.setVisible(False)
        self._error_label.setText("")
        self.setWindowTitle(tr("admin.edit_product") if product is not None else tr("admin.add_product"))
        shown = draft if draft is not None else product

        self._barcode_input.setText(shown.barcode if shown is not None else "")
        # Barcode is the primary key - fixed once the product exists, so
        # it's only editable while adding.
        self._barcode_input.setEnabled(product is None)

        self._name_input.setText(shown.name if shown is not None else "")
        self._price_input.setValue(shown.price if shown is not None else 0.0)

        self._stock_input.setValue(shown.stock_quantity if shown is not None else 0)
        # Editable only while adding: a brand-new product's starting
        # count is just its initial value, not a "change" to anything.
        # Once a product exists, stock only ever moves via a
        # sale/receive/dispatch (see product_repository.update()'s
        # docstring) - never a direct edit here, so this field is shown
        # for context in Edit mode but disabled, and update() ignores it
        # regardless of what it holds.
        self._stock_input.setEnabled(product is None)

        self._critical_input.setValue(shown.critical_stock_level if shown is not None else 0)

    def validation_error(self) -> str | None:
        """What is wrong with the form as filled in (None: fine to save).
        A price of 0.00 is rejected here - it is almost always an unset
        field, and a product would sell for free; the repository itself
        still accepts 0 for callers that really mean it."""
        if not self._editing_barcode:
            try:
                normalise_barcode(self._barcode_input.text())
            except ValueError:
                return tr("admin.form_err_barcode")
        if not self._name_input.text().strip():
            return tr("admin.form_err_name")
        if self._price_input.value() <= 0:
            return tr("admin.form_err_price")
        return None

    def accept(self) -> None:
        """Save, unless the form is invalid - then stay open and say why."""
        error = self.validation_error()
        if error:
            self._error_label.setText(error)
            self._error_label.setVisible(True)
            return
        self._error_label.setVisible(False)
        super().accept()

    def is_editing(self) -> bool:
        return self._editing_barcode is not None

    def result_product(self) -> Product:
        """Read the form back out as a Product.

        Caller checks is_editing() to decide between
        product_repository.create() and .update() - this popup has no
        opinion on which, and no database import of its own.
        """
        return Product(
            barcode=self._barcode_input.text().strip(),
            name=self._name_input.text().strip(),
            price=self._price_input.value(),
            stock_quantity=self._stock_input.value(),
            critical_stock_level=self._critical_input.value(),
            is_active=self._editing_active,
        )
