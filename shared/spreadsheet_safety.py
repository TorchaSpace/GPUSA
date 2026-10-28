"""Keeping exported text from being treated as a spreadsheet formula.

A product, dealership or store name is typed by people (or pasted); one
that starts with = + - or @ would run as a formula when the export is
opened in Excel/Numbers/LibreOffice. safe_cell() leaves numbers and
percentages alone (a genuine "-3.2%" must stay readable) and defuses the
rest with a leading apostrophe. Control characters - which Excel files
cannot hold at all - are dropped.
"""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_NUMBERISH = re.compile(r"^[+-]?[\d.,\s]+%?$")
_RISKY_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    if not isinstance(value, str):
        return value
    text = _CONTROL.sub("", value)
    if text.startswith(_RISKY_START) and not _NUMBERISH.match(text):
        return "'" + text
    return text


def excel_text(value):
    """Text for an .xlsx cell: control characters (which an Excel file cannot
    hold) are dropped, nothing else is touched. The exporter stores it as a
    plain string cell (data_type "s"), which a spreadsheet never evaluates -
    so no defusing apostrophe is needed and names show exactly as typed.
    safe_cell stays for CSV and PDF, where there is no such cell type."""
    if not isinstance(value, str):
        return value
    return _CONTROL.sub("", value)
