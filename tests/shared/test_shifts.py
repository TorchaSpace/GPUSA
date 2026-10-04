from datetime import datetime

from shared.shifts import shift_for


def test_shift_boundaries():
    at = lambda h, m=0: datetime(2026, 10, 4, h, m)  # noqa: E731
    assert shift_for(at(5, 59)) == "C"
    assert shift_for(at(6, 0)) == "A"
    assert shift_for(at(13, 59)) == "A"
    assert shift_for(at(14, 0)) == "B"
    assert shift_for(at(21, 59)) == "B"
    assert shift_for(at(22, 0)) == "C"
    assert shift_for(at(0, 0)) == "C"


def test_turkish_upper_keeps_the_dotted_capital():
    from shared import i18n
    from shared.textcase import upper

    try:
        i18n.set_language("tr")
        assert upper("giriş çıkış") == "GİRİŞ ÇIKIŞ"
        i18n.set_language("en")
        assert upper("in out") == "IN OUT"
    finally:
        i18n.set_language("en")
