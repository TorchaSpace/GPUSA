"""File-order checks always run; the real-PostgreSQL test runs only when
GPUSA_TEST_DATABASE_URL points at a throwaway database."""

import os

import pytest

from server import migrate


def test_migrations_are_numbered_and_ordered():
    names = [p.name for p in migrate.migration_files()]
    assert names and names == sorted(names)
    assert names[0].startswith("0001_")


@pytest.mark.skipif(not os.environ.get("GPUSA_TEST_DATABASE_URL"), reason="needs a test PostgreSQL")
def test_migrations_apply_once_on_postgres():
    import psycopg

    with psycopg.connect(os.environ["GPUSA_TEST_DATABASE_URL"]) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        first = migrate.run_migrations(conn)
        assert first and migrate.run_migrations(conn) == []
        conn.execute("INSERT INTO devices (id, kind, name, token_hash) VALUES (gen_random_uuid(), 'pos', 'k', 'h')")
