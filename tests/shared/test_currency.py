"""Currency choice: legacy look until chosen, one symbol everywhere afterwards."""

import pytest

from shared import currency
from shared import store_settings as ss


@pytest.fixture(autouse=True)
def _reset():
    currency.set_currency(None)
    yield
    currency.set_currency(None)


def test_unset_keeps_each_screens_legacy_symbol():
    assert currency.format_money(10, "$") == "$10.00"
    assert currency.format_money(10) == "10.00"


def test_chosen_currency_overrides_legacy():
    currency.set_currency("EUR")
    assert currency.format_money(10, "$") == "€10.00"
    assert currency.format_money(10) == "€10.00"
    currency.set_currency("NONE")
    assert currency.format_money(10, "$") == "10.00"


def test_negative_and_none_values():
    currency.set_currency("GBP")
    assert currency.format_money(-5) == "-£5.00"
    assert currency.format_money(None) == currency.format_amount(None)


def test_unknown_code_refused():
    with pytest.raises(ValueError):
        currency.set_currency("XXX")


def test_currency_from_settings():
    assert ss.currency_from({}) is None
    assert ss.currency_from({ss.KEY_CURRENCY: "try"}) == "TRY"
    assert ss.currency_from({ss.KEY_CURRENCY: "bogus"}) is None
