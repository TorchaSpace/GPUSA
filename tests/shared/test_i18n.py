from shared import i18n
from shared import store_settings as ss


def test_every_turkish_key_has_an_english_original():
    assert set(i18n._STRINGS["tr"]) <= set(i18n._STRINGS["en"])


def test_placeholders_match_between_languages():
    import re

    for key, text in i18n._STRINGS["tr"].items():
        assert sorted(re.findall(r"\{\w+\}", text)) == sorted(re.findall(r"\{\w+\}", i18n._STRINGS["en"][key])), key


def test_language_switch_and_fallback():
    try:
        i18n.set_language("tr")
        assert i18n.tr("nav.settings") == "Ayarlar"
        assert i18n.tr("admin.no_selection_title") == "Ürün seçilmedi"
        i18n._STRINGS["en"]["test.only_english"] = "Only English"
        assert i18n.tr("test.only_english") == "Only English"  # untranslated -> English, never blank
        assert i18n.tr("no.such.key") == "no.such.key"
    finally:
        i18n._STRINGS["en"].pop("test.only_english", None)
        i18n.set_language("en")
    assert i18n.tr("nav.settings") == "Settings"


def test_saved_language_defaults_to_english_when_unknown():
    assert ss.language_from({}) == "en"
    assert ss.language_from({ss.KEY_LANGUAGE: "TR"}) == "tr"
    assert ss.language_from({ss.KEY_LANGUAGE: "xx"}) == "en"


# --- coverage and typo guards for the whole Turkish admin panel ----------------

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("admin_app", "pos_app", "depot_app", "shared", "database")
EN = i18n._STRINGS["en"]
TR_TABLE = i18n._STRINGS["tr"]

# call name -> how its first string argument maps to table keys
_KEY_CALLS = {"tr": "", "tr_or": "", "_localize": "", "localized_auth": "", "UserError": "", "english": ""}
_SUFFIX_CALLS = {"plural": ("_one", "_other")}
_GROUP_CALLS = {"enum_label": "enum.", "region_label": None}


def _call_name(func) -> str | None:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _scan_keys():
    """Yield (path, line, kind, text): literal keys passed to tr()/tr_or()/
    UserError()/..., plural() bases, enum_label() groups, LazyLabels()
    prefixes, and the constant head of f-string keys."""
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            if "installer" in path.parts or path.name in ("i18n.py", "i18n_admin_en.py", "i18n_admin_tr.py", "i18n_tr.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = _call_name(node.func)
                first = node.args[0]
                literal = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
                head = None
                if isinstance(first, ast.JoinedStr) and first.values and isinstance(first.values[0], ast.Constant):
                    head = first.values[0].value
                rel = path.relative_to(ROOT).as_posix()
                if name in _KEY_CALLS and name != "english":
                    if literal is not None:
                        yield rel, node.lineno, "key", literal
                    elif head:
                        yield rel, node.lineno, "head", head
                elif name in _SUFFIX_CALLS and literal is not None:
                    for suffix in _SUFFIX_CALLS[name]:
                        yield rel, node.lineno, "key", literal + suffix
                elif name == "enum_label" and literal is not None:
                    yield rel, node.lineno, "head", f"enum.{literal}."
                elif name == "LazyLabels" and literal is not None:
                    yield rel, node.lineno, "head", literal + "."


def test_every_key_used_in_code_exists_in_the_english_table():
    missing = []
    for rel, line, kind, text in _scan_keys():
        if kind == "key":
            if text not in EN:
                missing.append(f"{rel}:{line} {text!r}")
        elif not any(key.startswith(text) for key in EN):
            missing.append(f"{rel}:{line} (no key starts with {text!r})")
    assert not missing, "\n".join(missing)


def test_the_scan_actually_finds_keys():
    keys = [text for _, _, kind, text in _scan_keys() if kind == "key"]
    assert len(keys) > 400 and "admin.refresh" in keys and "err.sign_in_failed" in keys


def test_dynamic_key_families_are_complete_in_both_languages():
    from shared import models

    families = {
        "enum.region.": models.DEALERSHIP_REGIONS + ("Unassigned",),
        "enum.role.": models.EMPLOYEE_ROLES,
        "enum.location_type.": models.EMPLOYEE_LOCATION_TYPES,
        "enum.attendance.": ("Present", "Checked out", "Off"),
        "enum.doc_type.": models.LEDGER_DOC_TYPES,
        "enum.shipment_status.": models.SHIPMENT_STATUSES,
        "auth.role.": ("admin", "depot_manager", "cashier"),
        "auth.area.": ("admin", "depot_console", "pos"),
        "auth.area_name.": ("admin", "depot_console", "pos"),
        "enum.stock_status.": ("Out of stock", "Low stock", "In stock", "Inactive"),
        "enum.wh_status.": ("Near capacity", "Operational", "Capacity not set", "Inactive"),
        "enum.ledger_display.": ("Overdue", "Cleared", "Endorsed", "Pending", "Open"),
        "enum.ledger_filter.": ("All", "Pending", "Cleared", "Overdue"),
        "enum.ship_status.": ("Scheduled", "In Transit", "Arriving", "Delayed", "Delivered", "Cancelled"),
        "enum.po_status.": ("Awaiting approval", "Rejected", "Approved \u00b7 sent", "Sent"),
        "enum.period.": ("Month", "Quarter", "Year to date"),
        "enum.search_kind.": ("Product", "Dealership", "Warehouse", "Employee"),
        "format.month.": tuple(str(i) for i in range(1, 13)),
        "format.weekday.": tuple(str(i) for i in range(7)),
        "format.weekday_long.": tuple(str(i) for i in range(7)),
        "wh.reason.": ("receive", "dispatch", "shipment", "transfer", "count", "discrepancy", "floor"),
        "admin.settings.event.": ("sign_in", "sign_out", "failed", "locked", "refused", "pin_confirmed", "pin_changed",
                                  "account_created", "account_changed", "unlocked", "pin_reset", "recovery_code_created",
                                  "recovery_failed", "security_question_set"),
        "admin.settings.where.": ("admin", "depot_manager", "cashier"),
    }
    gaps = [prefix + code for prefix, codes in families.items() for code in codes
            if prefix + code not in EN or prefix + code not in TR_TABLE]
    assert not gaps, gaps


def test_admin_nav_and_header_keys_are_fully_translated():
    """Report: every admin.*, nav.*, header.*, page.* ... key has a Turkish text."""
    prefixes = ("admin.", "nav.", "header.", "page.", "settings.", "signin.", "recovery.", "question.", "search.",
                "reports.", "common.", "enum.", "format.", "err.", "auth.", "wh.", "treasury.", "analytics.",
                "distribution.", "route.")
    for prefix in prefixes:
        keys = [k for k in EN if k.startswith(prefix)]
        done = [k for k in keys if k in TR_TABLE]
        coverage = len(done) / len(keys) if keys else 1.0
        assert coverage >= 0.95, (prefix, coverage, [k for k in keys if k not in TR_TABLE][:10])
    admin = [k for k in EN if k.startswith("admin.")]
    assert all(k in TR_TABLE for k in admin), [k for k in admin if k not in TR_TABLE]
    assert len(set(EN) - set(TR_TABLE)) == 0


def test_turkish_texts_are_real_turkish_not_english_copies():
    same = [k for k, v in TR_TABLE.items() if v == EN[k] and len(v) > 25 and not v.startswith("<")]
    assert not same, same[:10]
    # the letters that are easy to lose: the table uses the real characters
    joined = "".join(TR_TABLE.values())
    for char in "\u015f\u011f\u0131\u0130\u00f6\u00fc\u00e7":
        assert char in joined, char
    assert "\ufffd" not in joined


def test_format_placeholders_in_turkish_are_the_same_names():
    for key, text in TR_TABLE.items():
        assert sorted(re.findall(r"\{\w+\}", text)) == sorted(re.findall(r"\{\w+\}", EN[key])), key


def test_all_turkish_texts_format_with_the_english_placeholders():
    for key, text in TR_TABLE.items():
        names = set(re.findall(r"\{(\w+)\}", EN[key]))
        text.format(**{name: 1 for name in names})


@pytest.fixture
def turkish():
    i18n.set_language("tr")
    try:
        yield
    finally:
        i18n.set_language("en")


def test_money_and_numbers_follow_the_language(turkish):
    from shared import formatting as f

    assert f.format_amount(1234.5) == "1.234,50"
    assert f.format_int(1234567) == "1.234.567"
    assert f.format_number(1234.56, 1) == "1.234,6"
    assert f.signed_int(1200) == "+1.200"
    i18n.set_language("en")
    assert f.format_amount(1234.5) == "1,234.50"
    assert f.format_int(1234567) == "1,234,567"
    assert f.signed_int(-3) == "-3"


def test_english_output_is_unchanged():
    from datetime import datetime, timezone

    from shared import formatting as f

    assert f.format_amount(None) == "\u2014" and f.format_amount(0) == "0.00"
    now = datetime(2026, 10, 4, 14, 5, tzinfo=timezone.utc)
    assert f.age_text("2026-10-04T12:00:00.000Z", now) == "2 h"
    assert f.age_text("2026-10-04T14:04:50.000Z", now) == "just now"
    assert f.local_datetime_text("2026-10-04T12:00:00.000Z")[2] == "."


def test_age_and_dates_in_turkish(turkish):
    from datetime import date, datetime, timezone

    from shared import formatting as f

    now = datetime(2026, 10, 4, 14, 5, tzinfo=timezone.utc)
    assert f.age_text("2026-10-04T12:00:00.000Z", now) == "2 sa"
    assert f.age_text("2026-10-04T14:04:50.000Z", now) == "az \u00f6nce"
    assert f.long_date_text(date(2026, 10, 4)) == "Paz 04 Eki 2026"
    assert f.day_month_text(date(2026, 9, 5)) == "05 Eyl"


def test_role_and_area_labels_follow_the_language(turkish):
    from shared import auth

    assert auth.ROLE_LABELS["admin"] == "Y\u00f6netici" and auth.role_label("cashier") == "Kasiyer"
    assert auth.AREA_LABELS.get("pos") == "kasay\u0131" and auth.AREA_LABELS.get("nope") is None
    assert auth.pin_problem("12", "cashier") == "Bu PIN, kasiyer hesaplar\u0131 i\u00e7in en az 4 haneli olmal\u0131."
    i18n.set_language("en")
    assert auth.ROLE_LABELS == {"admin": "Administrator", "depot_manager": "Depot manager", "cashier": "Cashier"}
    assert auth.pin_problem("12", "cashier") == "A cashier PIN needs at least 4 digits."


def test_user_error_is_a_value_error_with_english_args_and_translated_text():
    error = i18n.UserError("err.name_too_long", n=80)
    assert isinstance(error, ValueError)
    assert error.args[0] == "A name can be at most 80 characters." == str(error)
    i18n.set_language("tr")
    try:
        assert str(error) == "Ad en fazla 80 karakter olabilir."
    finally:
        i18n.set_language("en")


def _sample_exceptions():
    from database import exceptions as x

    return [
        x.ProductNotFoundError("A1"), x.DuplicateBarcodeError("A1"), x.TransactionNotFoundError(3),
        x.DealershipNotFoundError("D1"), x.DuplicateDealershipCodeError("D1"), x.EmployeeNotFoundError("E-1"),
        x.DuplicateBadgeIdError("E-1"), x.AlreadyCheckedInError("E-1"), x.NoOpenAttendanceRecordError("E-1"),
        x.InsufficientStockError("A1", 5, 2), x.InsufficientStockError("A1", 5, 2, "WH-01"),
        x.PurchaseOrderNotFoundError(4), x.PurchaseOrderAlreadyDecidedError("PO-1", "sent"),
        x.LedgerEntryNotFoundError(5), x.DuplicateLedgerDocumentError("C-1"), x.ShipmentNotFoundError(6),
        x.ShipmentStateError("SH-1", "in_transit", "dispatched"), x.WarehouseNotFoundError("WH-9"),
        x.DuplicateWarehouseCodeError("WH-1"), x.UnknownLocationError("warehouse", "WH-9"),
        x.LocationHasStockError("WH-1", 12), x.SignInFailedError(), x.SignInFailedError(1), x.SignInFailedError(2),
        x.AccountLockedError("04.10.2026 12:00"), x.AccountDisabledError(), x.NotAllowedError("Cashier", "the till"),
        x.AccountNotFoundError("B-1"), x.LastAdminError(), x.EmployeeInactiveError("E-1"), x.SessionInvalidError(),
        x.SelfActionError("switch off"), x.SelfActionError("remove"), x.SelfActionError("change the role of"),
        x.DealershipInactiveError("Bayi"), x.DealershipInactiveError(),
        x.LedgerEntryStateError(7, "cleared", "edit"), x.ProductInUseError("A1", "it appears on past sales"),
        x.LocationInUseError("warehouse", "WH-1", 1), x.LocationInUseError("dealership", "D1", 3),
        x.ShipmentNotDispatchedError("SH-2", "scheduled"), x.LocationInactiveError("WH-1"),
        x.CapacityExceededError("WH-1", 100, 90, 20), x.CapacityBelowUsageError("WH-1", 50, 80),
        x.ProductInactiveError("A1"),
    ]


def test_exception_messages_are_identical_in_english_to_what_they_always_said():
    for error in _sample_exceptions():
        assert str(error) == Exception.__str__(error), type(error).__name__


def test_exception_messages_are_translated_in_turkish(turkish):
    for error in _sample_exceptions():
        text = str(error)
        assert text != Exception.__str__(error), type(error).__name__
        assert "{" not in text and "}" not in text, text
    from database import exceptions as x

    assert str(x.SignInFailedError(1)) == "Rozet veya PIN yanl\u0131\u015f. Hesap kilitlenmeden \u00f6nce 1 deneme hakk\u0131 kald\u0131."
    assert str(x.InsufficientStockError("A1", 5, 2)).endswith("yaln\u0131zca 2 adet var")


def test_repository_validation_messages_are_translated_but_english_by_default(turkish):
    from database import dealership_repository as dealerships
    from shared.models import Dealership

    bad = Dealership(code="", name="x", region="Metro", city="c")
    with pytest.raises(ValueError) as caught:
        dealerships.validate_fields(bad)
    assert str(caught.value) == "Bir bayi kodu girin."
    i18n.set_language("en")
    with pytest.raises(ValueError) as caught:
        dealerships.validate_fields(bad)
    assert str(caught.value) == "Enter a dealership code."


def test_every_depot_text_has_a_turkish_translation():
    missing = [key for key in EN if key.startswith("depot.") and key not in TR_TABLE]
    assert not missing, missing[:20]
