"""Cross-cutting code shared by database/, pos_app/, and admin_app/.

Nothing in this package may import sqlite3, touch the filesystem for
anything but reading its own resources, or open a network connection.
That's what makes it safe for every other package to depend on: models,
constants, theming, i18n, pure document "builders", and generic GUI
mechanics all live here because they have no storage/network dependency
of their own.
"""
