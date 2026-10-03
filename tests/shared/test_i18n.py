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
        assert i18n.tr("admin.no_selection_title") == "No product selected"  # untranslated -> English, never blank
        assert i18n.tr("no.such.key") == "no.such.key"
    finally:
        i18n.set_language("en")
    assert i18n.tr("nav.settings") == "Settings"


def test_saved_language_defaults_to_english_when_unknown():
    assert ss.language_from({}) == "en"
    assert ss.language_from({ss.KEY_LANGUAGE: "TR"}) == "tr"
    assert ss.language_from({ss.KEY_LANGUAGE: "xx"}) == "en"
