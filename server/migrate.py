"""Apply server/migrations/*.sql in filename order, once each.

Applied files are recorded in schema_migrations; a Postgres advisory lock stops
two deploys migrating at the same time. Run: python -m server.migrate
"""

from __future__ import annotations

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_LOCK_ID = 7_340_201  # arbitrary, fixed


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def run_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the names applied this run."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        for path in migration_files(directory):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def main() -> None:
    from server.config import load_settings
    from server.db import connect

    with connect(load_settings()) as conn:
        names = run_migrations(conn)
    print("applied:", ", ".join(names) if names else "nothing (up to date)")


if __name__ == "__main__":
    main()
