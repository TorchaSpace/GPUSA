"""CSS font stacks for the Qt style sheets.

A style sheet's `font-family: 'A, B, C'` - the whole list inside ONE pair of
quotes - names a single family called "A, B, C", which no machine has, so
the fallbacks never apply and Qt silently uses its default font. These
turn a plain comma-separated list ("Lora, Georgia, serif") into the form
Qt reads as a real fallback list ("'Lora', 'Georgia', serif"): each family
quoted on its own, generic keywords left bare.

Pure functions - no Qt - so they are testable anywhere.
"""

from __future__ import annotations

GENERIC_FAMILIES = frozenset({"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"})


def css_font_stack(spec: str) -> str:
    """"Lora, Constantia, serif" -> "'Lora', 'Constantia', serif"."""
    families = [part.strip().strip("'\"").strip() for part in spec.split(",")]
    families = [name for name in families if name]
    if not families:
        raise ValueError("a font stack needs at least one family")
    return ", ".join(name if name.lower() in GENERIC_FAMILIES else f"'{name}'" for name in families)
