import pytest

from shared import store_settings as ss


def test_defaults_when_nothing_is_set():
    assert ss.profile_from({}) == ss.StoreProfile("GPUSA", ())
    assert ss.prefs_from({}) == ss.NotificationPrefs(True, True)


def test_profile_round_trip():
    profile = ss.validate_profile("  Harbor   Point  ", "1 Quay St\n\n  Norfolk \n")
    assert profile == ss.StoreProfile("Harbor Point", ("1 Quay St", "Norfolk"))
    assert ss.profile_from(ss.profile_to_values(profile)) == profile


def test_blank_name_and_overlong_input_are_refused():
    with pytest.raises(ValueError):
        ss.validate_profile("   ", "")
    with pytest.raises(ValueError):
        ss.validate_profile("x" * 61, "")
    with pytest.raises(ValueError):
        ss.validate_profile("Shop", "\n".join(["line"] * 5))
    with pytest.raises(ValueError):
        ss.validate_profile("Shop", "y" * 41)


def test_prefs_round_trip_and_unknown_text_defaults_on():
    prefs = ss.NotificationPrefs(low_stock_alerts=False, pending_approvals=True)
    assert ss.prefs_from(ss.prefs_to_values(prefs)) == prefs
    assert ss.prefs_from({ss.KEY_LOW_STOCK_ALERTS: "garbage"}).low_stock_alerts is True
    assert ss.prefs_from({ss.KEY_PENDING_BADGE: "off"}).pending_approvals is False
