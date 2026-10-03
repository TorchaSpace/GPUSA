from shared.spreadsheet_safety import safe_cell


def test_formulas_are_defused():
    assert safe_cell('=HYPERLINK("http://x","y")') == '\'=HYPERLINK("http://x","y")'
    assert safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert safe_cell("-cmd|' /C calc'!A0") == "'-cmd|' /C calc'!A0"
    assert safe_cell("+1+1x") == "'+1+1x"


def test_numbers_percentages_and_normal_text_are_untouched():
    for text in ("-3.2%", "+4%", "-1,250.50", "1,250", "Carton", "Şişli", ""):
        assert safe_cell(text) == text
    assert safe_cell(12.5) == 12.5 and safe_cell(None) is None


def test_control_characters_are_removed():
    assert safe_cell("Box\x07 A") == "Box A"
