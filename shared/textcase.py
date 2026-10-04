"""Upper-casing that is right for Turkish. Python's str.upper() turns "giriş" into
"GIRIŞ"; in Turkish the dotted i has a dotted capital, "GİRİŞ" (and "ı" -> "I")."""

from __future__ import annotations


def upper(text: str) -> str:
    from shared import i18n  # imported here: i18n needs no widgets, but keep this module import-light

    if i18n.current_language() == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()
