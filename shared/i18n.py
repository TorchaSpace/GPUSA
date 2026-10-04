"""Single lookup function for every user-facing string.

GUI, builder, and export code must call tr("some.key") rather than
hardcoding English text inline. Wiring this in from day one means adding
a second language later is a matter of adding a language table here, not
hunting through every screen for string literals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from shared.i18n_admin_en import EN_ADMIN
from shared.i18n_depot import EN_DEPOT, TR_DEPOT
from shared.i18n_pos import EN_POS, TR_POS
from shared.i18n_tr import TR

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
        # Admin shell
        "admin.brand_subtitle": "Admin Dashboard",
        "nav.section.operations": "Operations",
        "nav.section.people": "People & records",
        "nav.overview": "Overview",
        "nav.inventory": "Inventory",
        "nav.warehouses": "Warehouses",
        "nav.dealerships": "Dealerships",
        "nav.distribution": "Distribution",
        "nav.purchase_requests": "Purchase requests",
        "nav.workforce": "Workforce",
        "nav.reports": "Reports",
        "nav.treasury": "Treasury & Ledger",
        "nav.settings": "Settings",
        "page.overview.title": "Operations Overview",
        "page.overview.subtitle": "Sales, stock and approvals at a glance.",
        "page.inventory.title": "Inventory Management",
        "page.inventory.subtitle": "Every product, its price and how much is left where.",
        "page.warehouses.title": "Warehouses",
        "page.warehouses.subtitle": "Capacity, stock and staff for each site.",
        "page.dealerships.title": "Dealership Network",
        "page.dealerships.subtitle": "Your dealers, where they are and how they sell.",
        "page.distribution.title": "Distribution Network",
        "page.distribution.subtitle": "Shipments on the road and what is due next.",
        "page.purchase_requests.title": "Purchase Requests",
        "page.purchase_requests.subtitle": "Review, approve or decline what the depots ask for.",
        "page.workforce.title": "Workforce Management",
        "page.workforce.subtitle": "Who works where, and who is on shift today.",
        "page.reports.title": "Analytics & Reports",
        "page.reports.subtitle": "Revenue trends and exports for any period.",
        "page.treasury.title": "Treasury & Ledger",
        "page.treasury.subtitle": "Money in, money out and what is overdue.",
        "page.settings.title": "Settings",
        "page.settings.subtitle": "Store details, alerts, data location and who can sign in.",
        "header.search": "Search",
        "header.pending_approvals": "Pending approvals",
        "header.search_tip": "Find a product, dealership, warehouse or person",
        "reports.sales_by_product": "Sales by product",
        "reports.sales_by_product_tip": "Per-product sales for any date range",
        "search.hint": "Search products, dealerships, warehouses, people",
        "search.no_matches": "No matches.",
        "search.title": "Search",
        # Settings
        "settings.store.kicker": "Store",
        "settings.general": "General",
        "settings.store_name": "Store name",
        "settings.address": "Address",
        "settings.address_hint": "One line per row, up to 4 (street, city, phone ...)",
        "settings.store_note": "Printed at the top of every till receipt and every exported report.",
        "settings.language": "Language",
        "settings.language_note": "Admin restarts to apply a new language - you will be asked after saving.",
        "settings.alerts.kicker": "Alerts",
        "settings.notifications": "Notifications",
        "settings.low_stock_alerts": "Show the low-stock alert banner on the depot floor",
        "settings.pending_badge": "Show the pending-approvals count on Purchase requests and page headers",
        "settings.notifications_note": "Turning an alert off only hides it; purchase requests and stock levels are unaffected.",
        "settings.storage.kicker": "Storage",
        "settings.data_location": "Data location",
        "settings.change_folder": "Change folder...",
        # Forgotten PIN
        "signin.forgot": "Forgot your PIN or badge ID?",
        "recovery.erase_intro": "No way back in is set up for this account. You can start over: everything is saved to a backup file beside the database, the app is emptied, and you create a new administrator.",
        "recovery.erase_confirm": "I understand all data will be erased (a backup file is kept)",
        "recovery.erase": "Erase and start over",
        "recovery.title": "Get back into Admin",
        "recovery.step1": "To protect your data, a PIN can only be reset by someone who can reach this computer's files. Create an empty file named {name} in this folder, then press Continue:",
        "recovery.open_folder": "Open the folder",
        "recovery.continue": "Continue",
        "recovery.not_found": "The file {name} isn't in that folder yet.",
        "recovery.step2": "File found. Choose the administrator and set a new PIN.",
        "recovery.admin": "Administrator",
        "recovery.new_pin": "New PIN ({n}+ digits)",
        "recovery.pin_again": "New PIN again",
        "recovery.mismatch": "The two PINs don't match.",
        "recovery.no_admins": "There is no active administrator. Close this window and restart Admin to create one.",
        "recovery.set_pin": "Set new PIN",
        "recovery.done": "New PIN saved. Sign in with it now.",
        "common.close": "Close",
        "recovery.code_intro": "Type the recovery code you saved, choose the administrator and set a new PIN.",
        "recovery.code": "Recovery code",
        "recovery.no_code": "I don't have the recovery code",
        "recovery.have_code": "I have the recovery code",
        "recovery.code_title": "Your recovery code",
        "recovery.code_body": "If you ever forget the administrator PIN, this code gets you back in. It is shown only now. Write it on paper and keep it somewhere safe - not on this computer. Making a new code later cancels this one.",
        "recovery.code_saved": "I've written it down",
        "settings.recovery_code": "Recovery code",
        "settings.recovery_code_confirm": "Make a new recovery code? The old one stops working.",
        "question.label": "Security question",
        "question.answer": "Your answer",
        "question.first_admin_hint": "If you ever forget the PIN, you will answer this question to set a new one. Pick something only you know.",
        "question.preset.pet": "What was the name of your first pet?",
        "question.preset.school": "What was the name of your primary school?",
        "question.preset.street": "What street did you grow up on?",
        "question.preset.teacher": "What was your favourite teacher's surname?",
        "question.preset.city": "In what city did you meet your closest friend?",
        "recovery.question_intro": "Answer your security question, choose the administrator and set a new PIN.",
        "recovery.another_way": "Try another way",
        "settings.security_question": "Security question",
        "settings.security_question_title": "Set your security question",
        "settings.security_question_pin": "Your current PIN",
        "settings.security_question_saved": "Saved. You can now use \"Forgot your PIN?\" on the sign-in screen.",
        # Shared
        "common.total": "Total",
        "common.cancel": "Cancel",
        "common.save": "Save",
        **EN_ADMIN,
        **EN_DEPOT,
        **EN_POS,
    },
    "tr": {**TR, **TR_DEPOT, **TR_POS},
}

LANGUAGE_NAMES = {"en": "English", "tr": "T\u00fcrk\u00e7e"}


def available_languages() -> list[tuple[str, str]]:
    """(code, name in that language) for the language picker."""
    return [(code, LANGUAGE_NAMES.get(code, code)) for code in _STRINGS]


def current_language() -> str:
    return _CURRENT_LANGUAGE


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


def plural(base_key: str, n: int) -> str:
    """"{n} SKU" / "{n} SKUs": tr(base_key + "_one" | "_other") formatted with n."""
    return tr(base_key + ("_one" if n == 1 else "_other")).format(n=n)


def enum_label(group: str, code: str | None) -> str:
    """Display label for a stored enum code (region, role, ...): the
    translation of "enum.<group>.<code>", or the code itself when there is
    none. The code stays English in the database; only the label moves."""
    if not code:
        return code or ""
    key = f"enum.{group}.{code}"
    text = tr(key)
    return code if text == key else text


def region_label(code: str | None) -> str:
    return enum_label("region", code)


class LazyLabels(Mapping):
    """A read-only {code: label} mapping whose labels are looked up with
    tr("<prefix>.<code>") at access time, so a dict that used to hold
    English text follows the language chosen at start-up. Behaves like the
    dict it replaces for [], .get(), `in`, iteration and ==."""

    def __init__(self, prefix: str, codes: Iterable[str]):
        self._prefix = prefix
        self._codes = tuple(codes)

    def __getitem__(self, code: str) -> str:
        if code not in self._codes:
            raise KeyError(code)
        return tr(f"{self._prefix}.{code}")

    def __iter__(self):
        return iter(self._codes)

    def __len__(self) -> int:
        return len(self._codes)

    def __repr__(self) -> str:
        return repr(dict(self))


def english(key: str, **values) -> str:
    """The English text for `key`, formatted - what a message said before it
    was translated (kept as the exception's args[0] so logs and tests that
    read the English still work)."""
    return _STRINGS["en"][key].format(**values)


class UserError(ValueError):
    """A ValueError whose message is meant for the person at the screen:
    str() looks the key up in the language the app runs in, with the same
    values, so a validation message follows ui.language. args[0] is the
    English text."""

    def __init__(self, key: str, **values):
        super().__init__(english(key, **values))
        self.key = key
        self.values = values

    def __str__(self) -> str:
        text = tr(self.key)
        if text == self.key:
            return super().__str__()
        try:
            return text.format(**self.values)
        except (KeyError, IndexError, ValueError):
            return super().__str__()
