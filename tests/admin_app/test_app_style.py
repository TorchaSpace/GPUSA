from admin_app.gui.app_style import admin_extra_qss
from admin_app.theme import CLASSICAL_PALETTE


def test_stylesheet_is_complete_and_balanced():
    css = admin_extra_qss(CLASSICAL_PALETTE)
    assert css.count("{") == css.count("}")
    assert "None" not in css and "{p[" not in css
    for selector in ("QScrollBar:vertical", "QToolTip", "QComboBox", "QCheckBox::indicator:checked", "QLineEdit:focus"):
        assert selector in css
    assert CLASSICAL_PALETTE["accent"] in css
