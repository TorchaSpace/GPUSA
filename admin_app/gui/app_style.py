"""App-wide polish layered on top of shared/theme.py's base stylesheet:
everything Qt draws by itself that would otherwise look like a default
grey desktop widget - scroll bars, tooltips, text fields, drop-downs,
check boxes, menus, dialogs.

Follows the Classical design system's rules (see the mockup bundle's
readme): colour as stroke rather than fill, one accent, a themed hover and
pressed state on everything interactive, and a visible accent focus ring
(never the platform default). Pure string building - no Qt import - so it
can be tested anywhere.
"""

from __future__ import annotations


def admin_extra_qss(p: dict[str, str]) -> str:
    bg, surface, raised = p["background"], p["surface"], p["surface_raised"]
    border, text, muted, accent = p["border"], p["text_primary"], p["text_secondary"], p["accent"]
    radius = p.get("radius_md", "4px")
    accent_tint = "rgba(225, 173, 102, 30)"
    return f"""
        /* --- text entry ------------------------------------------------- */
        QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTimeEdit {{
            background-color: {surface}; color: {text};
            border: 1px solid {border}; border-radius: {radius};
            padding: 6px 10px; min-height: 20px;
            selection-background-color: {accent}; selection-color: {bg};
        }}
        QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover,
        QDateEdit:hover, QDateTimeEdit:hover, QTimeEdit:hover, QComboBox:hover {{ border-color: {muted}; }}
        QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
        QDateEdit:focus, QDateTimeEdit:focus, QTimeEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
        QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {muted}; }}
        QLineEdit[readOnly="true"] {{ color: {muted}; }}

        /* --- drop-downs --------------------------------------------------- */
        QComboBox {{
            background-color: {surface}; color: {text};
            border: 1px solid {border}; border-radius: {radius};
            padding: 5px 10px; min-height: 20px;
        }}
        QComboBox::drop-down {{ border: none; width: 22px; }}
        QComboBox QAbstractItemView {{
            background-color: {raised}; color: {text}; border: 1px solid {border};
            selection-background-color: {accent_tint}; selection-color: {accent}; outline: none; padding: 4px;
        }}

        /* --- check boxes and radio buttons --------------------------------- */
        QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 15px; height: 15px; border: 1px solid {muted}; background: transparent;
        }}
        QCheckBox::indicator {{ border-radius: 3px; }}
        QRadioButton::indicator {{ border-radius: 8px; }}
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background-color: {accent}; border-color: {accent};
        }}
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {border}; }}

        /* --- scroll bars: slim, quiet, accent on hover ------------------------ */
        QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
        QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 28px; }}
        QScrollBar::handle:horizontal {{ background: {border}; border-radius: 3px; min-width: 28px; }}
        QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {muted}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: none; border: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        /* --- tooltips, menus, message boxes ------------------------------------- */
        QToolTip {{
            background-color: {raised}; color: {text}; border: 1px solid {border};
            padding: 5px 8px; border-radius: {radius};
        }}
        QMenu {{ background-color: {raised}; color: {text}; border: 1px solid {border}; padding: 4px; }}
        QMenu::item {{ padding: 6px 18px; border-radius: 3px; }}
        QMenu::item:selected {{ background-color: {accent_tint}; color: {accent}; }}
        QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}
        QDialog, QMessageBox, QInputDialog {{ background-color: {surface}; }}
        QMessageBox QLabel, QInputDialog QLabel {{ font-size: 13px; }}
        QDialogButtonBox QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {{
            background-color: transparent; color: {text}; border: 1px solid {border};
            border-radius: {radius}; padding: 6px 16px; min-width: 72px;
        }}
        QDialogButtonBox QPushButton:hover, QMessageBox QPushButton:hover, QInputDialog QPushButton:hover {{
            border-color: {accent}; color: {accent};
        }}
        QDialogButtonBox QPushButton:default, QMessageBox QPushButton:default {{
            border-color: {accent}; color: {accent};
        }}
        QDialogButtonBox QPushButton:focus, QMessageBox QPushButton:focus {{ border: 1px solid {accent}; }}

        /* --- focus: a ring in the accent, never the platform default ------------ */
        QPushButton:focus {{ outline: none; }}
        QAbstractItemView {{ outline: none; }}
    """
