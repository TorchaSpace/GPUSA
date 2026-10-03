"""Depot-specific components.

Warehouse staff work scanner-in-hand on the floor, much like a cashier -
so these lean toward pos_app's large/high-contrast philosophy rather
than admin_app's dense one. Still its OWN files, not an import of
pos_app/gui/components/ - depot_app must never depend on pos_app (or
admin_app), only on shared/ and database/, even where the ergonomics
happen to be similar. Duplicating a small button subclass is cheap;
a cross-app import is a layering violation that's expensive to undo.
"""
