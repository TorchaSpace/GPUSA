"""Connection factory for the shared backend database.

Both desktop apps call get_connection() to obtain a connection to the
SAME shared_backend.db file, configured for concurrent access from two
separate processes. Nothing outside `database/` should call sqlite3
directly or hold its own connection - this is the one place that knows
the physical storage is SQLite at all.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from shared.i18n import UserError
from database.migrations import run_migrations
from shared.paths import get_db_path, resource_path, restrict_to_owner

# WAL mode allows one writer + multiple concurrent readers, which is the
# minimum needed for the POS app and Admin app to hit the same file at
# once. busy_timeout makes a writer that loses the race retry for a bit
# instead of raising `database is locked` immediately - a cashier
# finalizing a sale and an admin saving a price edit at the same instant
# should not have to fail the whole checkout.
_BUSY_TIMEOUT_MS = 5000

# schema.sql must be listed in both apps' PyInstaller `datas` (see
# pos_app/pos_app.spec, admin_app/admin_app.spec) so resource_path()
# still finds it once packaged - see shared/paths.py.
_SCHEMA_PATH = resource_path("database", "schema.sql")

_init_lock = Lock()
_initialized = False


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row


def _ensure_schema(conn: sqlite3.Connection) -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
        # Columns/data an older database is missing - see database/migrations.py.
        run_migrations(conn)
        _initialized = True
        main_db = conn.execute("PRAGMA database_list").fetchone()["file"]
        if main_db:
            restrict_to_owner(Path(main_db))


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a configured connection to the shared backend database.

    A caller (tests, mainly) may pass an explicit db_path to point at a
    temp/in-memory database instead of the real shared_backend.db - see
    tests/conftest.py. Every connection returned here is WAL-mode, has a
    busy_timeout set, and is guaranteed to have the schema applied.

    With no db_path given, resolves the shared, machine-wide location
    via shared.paths.get_db_path() - the same file both installed apps
    agree on regardless of where either was installed (see
    shared/paths.py for how that's configured).
    """
    path = db_path if db_path is not None else get_db_path()
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; repos manage transactions explicitly
    _configure(conn)
    _ensure_schema(conn)
    return conn


@contextmanager
def connection_scope(db_path: Path | None = None):
    """Yield a get_connection() result, closing it afterward.

    Every database/ repository function should open its connection via
    `with connection_scope() as conn:` rather than calling get_connection()
    directly and managing close() itself - this is what guarantees a
    repository function never leaks a connection on an early return or an
    exception, without every function having to remember its own
    try/finally. See database/product_repository.py for the pattern.
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def copy_database_to(destination: Path) -> None:
    """Write a consistent copy of the live database to `destination`
    (SQLite's online backup, so open apps and the WAL file are no problem).
    Raises FileExistsError if something is already there."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with connection_scope() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
    except BaseException:
        destination.unlink(missing_ok=True)  # never leave a stub that looks like a database
        raise


# What must be typed to confirm "start over" (either, any letter case).
ERASE_CONFIRM_WORDS = ("SIL", "ERASE")


def erase_confirmed(text: str | None) -> bool:
    return (text or "").strip().upper() in ERASE_CONFIRM_WORDS


def erase_all_data(confirmation: str | None = None) -> Path:
    """"Start over": copy the whole database to a timestamped backup file
    beside it, then empty every table (apps keep their connections; the
    schema stays). Returns the backup's path. Used only by Admin's "Forgot
    your PIN" last resort - it can destroy data, never reveal it, and it
    keeps a backup copy. `confirmation` must be one of ERASE_CONFIRM_WORDS
    (ValueError otherwise, nothing touched) so a stray call can't wipe
    the store."""
    from datetime import datetime

    if not erase_confirmed(confirmation):
        raise UserError("err.erase_confirm", words=" or ".join(ERASE_CONFIRM_WORDS))
    db_path = Path(get_db_path())
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    backup = db_path.with_name(f"{db_path.stem}.backup-{stamp}{db_path.suffix}")
    counter = 1
    while backup.exists():  # two start-overs in the same second must not collide
        counter += 1
        backup = db_path.with_name(f"{db_path.stem}.backup-{stamp}-{counter}{db_path.suffix}")
    copy_database_to(backup)
    with connection_scope() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")  # must be set outside a transaction
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Append-only guards (ledger_audit) would refuse the wipe: lift
            # them inside this transaction and put them back before commit.
            guard_rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg\\_%\\_no\\_%' ESCAPE '\\'"
            ).fetchall()
            guards = [row[1] for row in guard_rows]
            for row in guard_rows:
                conn.execute('DROP TRIGGER "%s"' % row[0].replace('"', '""'))
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:  # names come from sqlite_master itself, quoted anyway
                conn.execute('DELETE FROM "%s"' % table.replace('"', '""'))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                conn.execute("DELETE FROM sqlite_sequence")
            for sql in guards:
                conn.execute(sql)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
    return backup
