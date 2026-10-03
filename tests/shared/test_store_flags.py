"""store_settings flag parsing and the auth badge helper."""

from __future__ import annotations

from shared import auth
from shared import store_settings as ss


def test_notification_flags_are_case_insensitive():
    for off in ("0", "false", "False", "FALSE", "no", "No", "OFF", "off", " Off ", "\tNO\n"):
        prefs = ss.prefs_from({ss.KEY_LOW_STOCK_ALERTS: off, ss.KEY_PENDING_BADGE: off})
        assert prefs == ss.NotificationPrefs(False, False), off
    for on in ("1", "true", "True", "yes", "ON", "anything else"):
        prefs = ss.prefs_from({ss.KEY_LOW_STOCK_ALERTS: on, ss.KEY_PENDING_BADGE: on})
        assert prefs == ss.NotificationPrefs(True, True), on


def test_missing_or_blank_flags_fall_back_to_the_default():
    assert ss.prefs_from({}) == ss.NotificationPrefs(True, True)
    assert ss.prefs_from({ss.KEY_LOW_STOCK_ALERTS: "  "}) == ss.NotificationPrefs(True, True)


def test_flags_round_trip():
    for low in (True, False):
        for pending in (True, False):
            prefs = ss.NotificationPrefs(low, pending)
            assert ss.prefs_from(ss.prefs_to_values(prefs)) == prefs


def test_normalize_badge_id():
    assert auth.normalize_badge_id("  b-7 ") == "B-7"
    assert auth.normalize_badge_id("B-7") == "B-7"
    assert auth.normalize_badge_id(None) == "" and auth.normalize_badge_id("   ") == ""
