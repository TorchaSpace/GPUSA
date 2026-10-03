import pytest

from shared import security_question as sq


def test_answers_compare_loosely():
    assert sq.normalize_answer("  Şişli! ") == sq.normalize_answer("SISLI")
    assert sq.normalize_answer("İstanbul Kadıköy") == sq.normalize_answer("istanbul kadikoy")
    assert sq.normalize_answer("") == "" and sq.normalize_answer(None) == ""


def test_validation_messages():
    assert sq.validate("  Name of my   first pet? ", " Pamuk  ") == ("Name of my first pet?", "Pamuk")
    with pytest.raises(ValueError):
        sq.validate("short?", "Pamuk")
    with pytest.raises(ValueError):
        sq.validate("x" * 121, "Pamuk")
    with pytest.raises(ValueError):
        sq.validate("Name of my first pet?", "a!")


def test_every_preset_has_a_translation():
    from shared import i18n

    for key in sq.PRESET_KEYS:
        assert key in i18n._STRINGS["en"] and key in i18n._STRINGS["tr"]
