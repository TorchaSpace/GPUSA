"""Generic, app-agnostic PySide6 widget MECHANICS only.

This package owns *how a popup gets instantiated and refreshed in place*
and *how a tab wraps one visual plus its controls* - not how either one
looks or is sized. POS and Admin have deliberately different ergonomics
(large fast-action targets vs. compact data-dense layouts), so the
actual styled components live in each app's own gui/components/ package
and subclass/compose the base classes here. Nothing in this file should
set a fixed pixel size, font size, or padding value that expresses an
app's ergonomics - only the mechanism.
"""
