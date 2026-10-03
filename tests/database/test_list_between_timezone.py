"""list_between works in LOCAL time: a sale a little after local midnight
belongs to that local day even though the database stamped it the day
before in UTC."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import pytest

import database.connection as connection
from database import transaction_repository

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="time.tzset() is not available on Windows")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "get_db_path", lambda: tmp_path / "t.db")
    monkeypatch.setattr(connection, "_initialized", False)


@pytest.fixture
def istanbul():
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Istanbul"  # UTC+3 all year
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def test_sale_after_local_midnight_counts_on_the_local_day(istanbul):
    with connection.connection_scope() as conn:
        # 22:30 UTC on the 4th == 01:30 on the 5th in Istanbul.
        conn.execute("INSERT INTO transactions (created_at, total) VALUES ('2026-10-04T22:30:00.000Z', 10)")
    fifth = transaction_repository.list_between(datetime(2026, 10, 5), datetime(2026, 10, 6))
    fourth = transaction_repository.list_between(datetime(2026, 10, 4), datetime(2026, 10, 5))
    assert len(fifth) == 1 and fourth == []
    assert fifth[0].created_at == datetime(2026, 10, 5, 1, 30)
