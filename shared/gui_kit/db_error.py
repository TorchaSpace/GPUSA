"""The one dialog every app shows when the database file cannot be opened at
start-up (damaged, locked by something else, unreadable) - instead of a
silent crash with no window. Names the file and points at the backups."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from database.connection import active_db_path
from shared.i18n import tr


def show_database_error(error: BaseException) -> None:
    QMessageBox.critical(
        None,
        tr("admin.main.db_error"),
        tr("admin.main.db_error_body").format(path=active_db_path(), error=error),
    )
