"""Offscreen GUI tests for admin_app's Inventory page and its product form:
delete safety, deactivate / reactivate, the Show filter, validation errors."""

from __future__ import annotations

import pytest

from tests.gui_support import pump, qapp  # noqa: F401

import database.connection as connection
from database import product_repository, stock_repository, warehouse_repository
from shared.models import UNASSIGNED, Product, StockLocation, Warehouse


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def boxes(monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    seen = {"warning": [], "info": []}
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen["warning"].append(a[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: seen["info"].append(a[2]))
    return seen


@pytest.fixture
def page(qapp, boxes):
    product_repository.create(Product("BOX", "Carton", 40, 12, 5))
    product_repository.create(Product("NEW", "Never used", 3, 0, 0))
    product_repository.create(Product("OLD", "Retired", 9, 0, 0))
    product_repository.set_active("OLD", False)
    from admin_app.gui.pages.inventory_page import InventoryPage

    widget = InventoryPage()
    widget.show()
    pump(qapp)
    yield widget
    widget.close()


def _skus(page):
    model = page._table.model()
    return [model.index(r, 0).data() for r in range(model.rowCount())]


def _select(page, barcode):
    page._table.selectRow(_skus(page).index(barcode))


def test_inactive_products_are_listed_dimmed_with_a_badge(page):
    from PySide6.QtCore import Qt

    assert _skus(page) == ["BOX", "NEW", "OLD"]
    model = page._table.model()
    row = _skus(page).index("OLD")
    assert "inactive" in model.index(row, 1).data()
    assert model.index(row, 1).data(Qt.ForegroundRole) is not None
    assert "inactive" not in model.index(_skus(page).index("BOX"), 1).data()
    assert page._sku_count_label.text() == "2"  # "Active SKUs" counts only the active ones


def test_the_show_filter(page):
    combo = page._filter_combo
    combo.setCurrentIndex(combo.findData("active"))
    assert _skus(page) == ["BOX", "NEW"]
    combo.setCurrentIndex(combo.findData("inactive"))
    assert _skus(page) == ["OLD"]
    combo.setCurrentIndex(combo.findData("all"))
    assert _skus(page) == ["BOX", "NEW", "OLD"]


def test_filter_products_is_pure():
    from admin_app.gui.pages.inventory_page import filter_products

    products = [Product("A", "a", 1, 0), Product("B", "b", 1, 0, is_active=False)]
    assert [p.barcode for p in filter_products(products, "all")] == ["A", "B"]
    assert [p.barcode for p in filter_products(products, "active")] == ["A"]
    assert [p.barcode for p in filter_products(products, "inactive")] == ["B"]


def test_deactivate_and_reactivate_the_selected_product(page):
    _select(page, "BOX")
    assert page._detail_active_button.text() == "Deactivate"
    page._toggle_active_selected()
    assert product_repository.get_by_barcode("BOX").is_active is False
    _select(page, "BOX")
    assert page._detail_active_button.text() == "Reactivate"
    page._toggle_active_selected()
    assert product_repository.get_by_barcode("BOX").is_active is True


def test_deleting_a_product_with_stock_is_refused_with_a_clear_message(page, boxes):
    _select(page, "BOX")
    page._delete_selected()  # 12 units in stock: used to destroy them silently

    assert boxes["warning"] and "Deactivate" in boxes["warning"][0]
    assert product_repository.get_by_barcode("BOX").stock_quantity == 12


def test_deleting_a_product_with_history_does_not_crash(page, boxes):
    stock_repository.receive(UNASSIGNED, "NEW", 2)
    stock_repository.dispatch(UNASSIGNED, "NEW", 2)  # no stock left, history remains: used to be a raw IntegrityError
    page.reload()
    _select(page, "NEW")
    page._delete_selected()
    assert boxes["warning"] and "movements" in boxes["warning"][0]
    assert product_repository.get_by_barcode("NEW")


def test_deleting_an_unused_product_works(page):
    _select(page, "OLD")
    page._delete_selected()
    assert _skus(page) == ["BOX", "NEW"]


def test_a_rejected_save_is_reported_and_the_form_keeps_what_was_typed(page, boxes):
    page._open_add_popup()
    form = page._popup
    form._barcode_input.setText("X-1")
    form._name_input.setText("Valid")
    form._price_input.setValue(5.0)
    product = form.result_product()
    product.critical_stock_level = -4  # (the spin box can't, but a repository caller could) -> ValueError
    page._popup.result_product = lambda: product  # force the failing path through _save_popup
    page._save_popup()

    assert boxes["warning"] and "negative" in boxes["warning"][0]
    assert page._popup.isVisible()  # re-opened for correction
    assert product_repository.list_all() and all(p.barcode != "X-1" for p in product_repository.list_all())


def test_duplicate_barcode_in_any_case_is_a_clean_message(page, boxes):
    page._open_add_popup()
    form = page._popup
    form._barcode_input.setText("box")
    form._name_input.setText("Again")
    form._price_input.setValue(5.0)
    page._save_popup()
    assert boxes["warning"] and "already exists" in boxes["warning"][0]


def test_detail_text_is_network_wide_and_not_the_stale_note(page):
    _select(page, "BOX")
    texts = []
    from PySide6.QtWidgets import QLabel

    for label in page._detail_panel.findChildren(QLabel):
        texts.append(label.text())
    joined = " ".join(texts)
    assert "isn't tracked yet" not in joined
    assert "NETWORK-WIDE" in joined and "Status (network-wide)" in joined


# --- the product form -------------------------------------------------------------

def test_form_rejects_a_zero_price_and_stays_open(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=None)
    accepted = []
    form.accepted.connect(lambda: accepted.append(True))
    form._barcode_input.setText("A-1")
    form._name_input.setText("Thing")
    form._price_input.setValue(0.0)
    form.accept()
    assert accepted == [] and form.isVisible()
    assert "above 0.00" in form._error_label.text() and not form._error_label.isHidden()

    form._price_input.setValue(1.5)
    form.accept()
    assert accepted == [True]
    form.close()


def test_form_requires_barcode_and_name(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=None)
    form._price_input.setValue(2)
    assert "barcode" in form.validation_error()
    form._barcode_input.setText("   ")
    assert "barcode" in form.validation_error()
    form._barcode_input.setText("Z-1")
    assert "name" in form.validation_error()
    form._name_input.setText("  ")
    assert "name" in form.validation_error()
    form._name_input.setText("Ok")
    assert form.validation_error() is None
    form.close()


def test_editing_keeps_the_active_flag_and_shows_a_note_for_inactive_products(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=Product("OLD", "Retired", 9, 0, 0, is_active=False))
    assert not form._inactive_label.isHidden()
    assert form.result_product().is_active is False
    form.open_or_refresh(product=Product("BOX", "Carton", 40, 12, 5))
    assert form._inactive_label.isHidden() and form.result_product().is_active is True
    form.close()


def test_a_draft_refills_the_form_after_a_failed_save(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=None, draft=Product("TYPED", "What I typed", 7.25, 3, 2))
    assert (form._barcode_input.text(), form._name_input.text(), form._price_input.value()) == ("TYPED", "What I typed", 7.25)
    assert form.is_editing() is False  # still Add mode
    form.close()


def test_initial_stock_from_the_form_leaves_an_audit_row(page):
    page._open_add_popup()
    form = page._popup
    form._barcode_input.setText("NEW-2")
    form._name_input.setText("Fresh")
    form._price_input.setValue(2.5)
    form._stock_input.setValue(8)
    form.accept()  # emits accepted -> InventoryPage._save_popup
    assert product_repository.get_by_barcode("NEW-2").stock_quantity == 8
    [movement] = [m for m in stock_repository.list_movements() if m["barcode"] == "NEW-2"]
    assert movement["quantity"] == 8 and "Initial stock" in movement["note"]


# --- unit cost and margin -------------------------------------------------


def test_table_shows_cost_and_margin_and_dashes_while_cost_is_unknown(page):
    product_repository.set_cost("BOX", 30)
    page.reload()
    model = page._table.model()
    box = _skus(page).index("BOX")
    new = _skus(page).index("NEW")
    assert model.index(box, 5).data() == "30.00"
    assert model.index(box, 6).data() == "25.0%"  # (40 - 30) / 40
    assert model.index(new, 5).data() == "—"
    assert model.index(new, 6).data() == "—"


def test_margin_helpers_flag_a_loss_in_red_and_leave_the_rest_alone():
    from admin_app.gui.components.inventory_table import cost_text, margin_color, margin_text

    losing = Product("L", "Loss", 10, 0, 0, cost_price=12.5)
    fine = Product("F", "Fine", 10, 0, 0, cost_price=5)
    unknown = Product("U", "Unknown", 10, 0, 0)
    assert margin_text(losing) == "-25.0%" and margin_color(losing) is not None
    assert margin_text(fine) == "50.0%" and margin_color(fine) is None
    assert margin_text(unknown) == "—" and margin_color(unknown) is None
    assert cost_text(unknown) == "—" and cost_text(losing) == "12.50"


def test_form_warns_when_cost_is_above_price_but_still_saves(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=Product("BOX", "Carton", 40, 12, 5))
    assert form.cost_warning() is None and form._cost_warning.isHidden()
    form._cost_input.setValue(55)
    assert form.cost_warning() is not None and not form._cost_warning.isHidden()
    assert form.validation_error() is None  # a warning, not an error
    form._cost_input.setValue(10)
    assert form.cost_warning() is None and form._cost_warning.isHidden()
    form.close()


def test_form_cost_changed_only_when_the_field_was_edited(qapp):
    from admin_app.gui.components.product_form_popup import ProductFormPopup

    form = ProductFormPopup()
    form.open_or_refresh(product=Product("BOX", "Carton", 40, 12, 5, cost_price=30))
    assert form.cost_changed() is False
    form._cost_input.setValue(31.5)
    assert form.cost_changed() is True
    assert form.result_product().cost_price == 31.5
    form.close()


def test_editing_the_cost_in_the_form_is_saved(page):
    _select(page, "BOX")
    page._open_edit_popup()
    page._popup._cost_input.setValue(28.75)
    page._popup.accept()
    assert product_repository.get_by_barcode("BOX").cost_price == 28.75


def test_saving_the_form_untouched_does_not_overwrite_a_newer_cost(page):
    _select(page, "BOX")
    page._open_edit_popup()  # form opens with cost 0
    product_repository.set_cost("BOX", 22)  # e.g. a delivery updated it meanwhile
    page._popup._name_input.setText("Carton XL")
    page._popup.accept()
    saved = product_repository.get_by_barcode("BOX")
    assert saved.name == "Carton XL" and saved.cost_price == 22


def test_new_product_can_be_added_with_a_cost(page):
    page._open_add_popup()
    form = page._popup
    form._barcode_input.setText("COSTED")
    form._name_input.setText("Costed")
    form._price_input.setValue(10)
    form._cost_input.setValue(4)
    form.accept()
    assert product_repository.get_by_barcode("COSTED").cost_price == 4
