"""shared.fonts - the CSS font stacks the style sheets use."""

import pytest

from shared.fonts import css_font_stack


def test_each_family_is_quoted_on_its_own_and_generics_stay_bare():
    assert css_font_stack("Lora, Constantia, Georgia, serif") == "'Lora', 'Constantia', 'Georgia', serif"
    assert css_font_stack("Cormorant Garamond, Georgia, 'Times New Roman', serif") == (
        "'Cormorant Garamond', 'Georgia', 'Times New Roman', serif"
    )


def test_a_single_family_and_odd_spacing():
    assert css_font_stack("Segoe UI") == "'Segoe UI'"
    assert css_font_stack("  Menlo ,  monospace ") == "'Menlo', monospace"
    assert css_font_stack('"Helvetica Neue", Sans-Serif') == "'Helvetica Neue', Sans-Serif"


def test_empty_is_rejected():
    with pytest.raises(ValueError):
        css_font_stack(" , ")


def test_every_app_palette_carries_a_ready_to_use_css_stack():
    from admin_app.theme import CLASSICAL_PALETTE, FONT_HEADING, FONT_HEADING_CSS
    from depot_app.theme import INDUSTRY_PALETTE
    from depot_app.theme import FONT_HEADING_CSS as DEPOT_HEADING_CSS
    from pos_app.theme import ORGANIC_PALETTE
    from pos_app.theme import FONT_HEADING_CSS as POS_HEADING_CSS

    for palette in (CLASSICAL_PALETTE, INDUSTRY_PALETTE, ORGANIC_PALETTE):
        assert palette["font_family_css"] == css_font_stack(palette["font_family"])
        assert "', '" in palette["font_family_css"] or palette["font_family_css"].count("'") == 2
    assert FONT_HEADING_CSS == css_font_stack(FONT_HEADING)
    assert DEPOT_HEADING_CSS.endswith("sans-serif") and POS_HEADING_CSS.endswith("sans-serif")
    assert FONT_HEADING_CSS.endswith("serif")


def test_no_style_sheet_still_wraps_a_whole_font_list_in_one_pair_of_quotes():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for folder in ("admin_app", "pos_app", "depot_app", "shared"):
        for path in (root / folder).rglob("*.py"):
            if "build" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"font-family:\s*'\{(FONT_HEADING|FONT_BODY|font_family|p\['font_family'\])\}'", text):
                offenders.append(f"{path.relative_to(root)}: {match.group(0)}")
    assert offenders == []
