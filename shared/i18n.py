"""Single lookup function for every user-facing string.

GUI, builder, and export code must call tr("some.key") rather than
hardcoding English text inline. Wiring this in from day one means adding
a second language later is a matter of adding a language table here, not
hunting through every screen for string literals.
"""

from __future__ import annotations

_CURRENT_LANGUAGE = "en"

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # POS app
        "pos.window_title": "POS / Inventory System - Branch POS",
        "pos.scan_prompt": "Scan a product barcode...",
        "pos.complete_sale": "Complete Sale",
        "pos.cart_empty": "Cart is empty",
        "pos.low_stock_warning": "Low stock",
        # Depot app
        "depot.low_stock_panel": "Low Stock",
        "depot.receiving_tab": "Receive Stock",
        "depot.dispatch_tab": "Dispatch Stock",
        "depot.receive_action": "Receive",
        "depot.dispatch_action": "Dispatch",
        "depot.quantity_prompt": "Quantity",
        "depot.note_prompt": "Note (optional)",
        # Admin app
        "admin.window_title": "POS / Inventory System - Admin Dashboard",
        "admin.products_tab": "Products",
        "admin.price_updates_tab": "Price Updates",
        "admin.reports_tab": "Sales Reports",
        "admin.inventory_health_tab": "Inventory Health",
        "admin.add_product": "Add Product",
        "admin.edit_product": "Edit Product",
        "admin.delete_product": "Delete Product",
        "admin.refresh": "Refresh",
        "admin.no_selection_title": "No product selected",
        "admin.no_selection_body": "Select a product in the table first.",
        "admin.save_failed_title": "Couldn't save",
        "admin.confirm_delete_title": "Delete product?",
        "admin.confirm_delete_body": "Delete {name}? This cannot be undone.",
        "admin.form_barcode": "Barcode",
        "admin.form_name": "Name",
        "admin.form_price": "Price",
        "admin.form_stock": "Stock",
        "admin.form_critical_level": "Critical Level",
        "admin.apply_price": "Apply Price",
        "admin.generate_report": "Generate",
        "admin.export_pdf": "Export PDF",
        "admin.export_excel": "Export Excel",
        "admin.pdf_file_filter": "PDF Files (*.pdf)",
        "admin.excel_file_filter": "Excel Files (*.xlsx)",
        "admin.report_failed_title": "Report failed",
        "admin.no_report_title": "No report yet",
        "admin.no_report_body": "Press Generate first.",
        "admin.export_done_title": "Export complete",
        "admin.export_done_body": "Saved to {path}",
        "admin.add_dealership": "Add Dealership",
        "admin.edit_dealership": "Edit Dealership",
        "admin.form_code": "Code",
        "admin.form_region": "Region",
        "admin.form_city": "City",
        "admin.form_manager": "Manager",
        "admin.form_active": "Active",
        "admin.add_employee": "Add Employee",
        "admin.edit_employee": "Edit Employee",
        "admin.form_badge": "Badge ID",
        "admin.form_title": "Title",
        "admin.form_role": "Role",
        "admin.form_location_type": "Location type",
        "admin.form_location_name": "Location",
        # Shared
        "common.total": "Total",
        "common.cancel": "Cancel",
        "common.save": "Save",
    }
}


def set_language(language_code: str) -> None:
    if language_code not in _STRINGS:
        raise ValueError(f"No strings registered for language {language_code!r}")
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = language_code


def tr(key: str) -> str:
    """Look up a UI string by key in the current language.

    Falls back to English, then to the key itself, so a missing
    translation is visibly wrong in the UI rather than silently blank.
    """
    table = _STRINGS.get(_CURRENT_LANGUAGE, {})
    if key in table:
        return table[key]
    return _STRINGS["en"].get(key, key)
