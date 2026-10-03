"""CSS font stacks for the Qt style sheets.

A style sheet's `font-family: 'A, B, C'` - the whole list inside ONE pair of
quotes - names a single family called "A, B, C", which no machine has, so
the fallbacks never apply and Qt silently uses its default font. These
turn a plain comma-separated list ("Lora, Georgia, serif") into the form
Qt reads as a real fallback list ("'Lora', 'Georgia', serif"): each family
quoted on its own, generic keywords left bare.

Also resolves the bundled TTFs the PDF export embeds (report_font_files);
they are listed in shared/build_manifest.BUNDLED_DATA_FILES so a frozen
build carries them too.

Pure functions - no Qt - so they are testable anywhere.
"""

from __future__ import annotations

from pathlib import Path

from shared.paths import resource_path

GENERIC_FAMILIES = frozenset({"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"})


def css_font_stack(spec: str) -> str:
    """"Lora, Constantia, serif" -> "'Lora', 'Constantia', serif"."""
    families = [part.strip().strip("'\"").strip() for part in spec.split(",")]
    families = [name for name in families if name]
    if not families:
        raise ValueError("a font stack needs at least one family")
    return ", ".join(name if name.lower() in GENERIC_FAMILIES else f"'{name}'" for name in families)


REPORT_FONT_REGULAR = "DejaVuSans.ttf"
REPORT_FONT_BOLD = "DejaVuSans-Bold.ttf"


def report_font_dir() -> Path:
    """Folder holding the bundled report fonts (works frozen and not)."""
    return resource_path("shared", "assets", "fonts")


def report_font_files() -> tuple[Path, Path] | None:
    """(regular, bold) TTF paths for PDF exports, or None if either file is
    missing - the exporter then falls back to the built-in Helvetica."""
    folder = report_font_dir()
    regular, bold = folder / REPORT_FONT_REGULAR, folder / REPORT_FONT_BOLD
    return (regular, bold) if regular.is_file() and bold.is_file() else None
