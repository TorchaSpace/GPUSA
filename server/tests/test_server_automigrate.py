"""The server brings its own schema up to date when it starts."""

import os
import pathlib
import sys

import pytest

pytest.importorskip("alembic")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import psycopg  # noqa: E402

from gpusa_server import migrate  # noqa: E402
from gpusa_server.config import Settings  # noqa: E402

URL = os.environ.get("GPUSA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GPUSA_TEST_DATABASE_URL to test migrations")


def test_upgrade_to_head_adds_a_missing_column():
    settings = Settings(database_url=URL)
    migrate.upgrade_to_head(settings)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("ALTER TABLE accounts DROP COLUMN is_root")
        conn.execute("UPDATE alembic_version SET version_num = '0002'")
    migrate.upgrade_to_head(settings)
    with psycopg.connect(URL) as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'accounts'")}
    assert "is_root" in cols


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("GPUSA_AUTO_MIGRATE", "0")
    assert not migrate.enabled(Settings(database_url=URL))
    monkeypatch.delenv("GPUSA_AUTO_MIGRATE")
    assert migrate.enabled(Settings(database_url=URL))
