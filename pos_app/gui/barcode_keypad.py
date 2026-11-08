"""Manual barcode entry - the design's "Barcode Keypad" overlay.

When a label is torn or the scanner can't read it, the cashier taps the
keyboard button in the header and types the number printed under the
barcode on a big touch keypad (or on a real keyboard: digits, letters for
SKU-style codes, Backspace, Enter, Esc). "Add to sale" puts the product in
the open sale; an unknown, sold-out or already-maxed code shakes the field
and says why in red, and the keypad stays open so the code can be fixed.

The overlay sits over the whole window (a 72% ink scrim, like the design's
absolute inset:0 layer); tapping the scrim or the X closes it. It only
collects the code: MainWindow looks it up through NewSalePage.add_by_barcode()
and calls show_error() / close_keypad() with the outcome.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRect, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pos_app.gui import icons
from pos_app.gui.components.organic import PillButton, soft_shadow
from pos_app.theme import FONT_HEADING_CSS, ORGANIC_PALETTE
from shared.gui_kit.motion import animations_enabled, fade_in
from shared.i18n import tr
from shared.textcase import upper

# Design tokens (styles.css of the Organic design system).
_NEUTRAL_100 = "#f9f4ed"
_NEUTRAL_200 = "#eee7db"
_NEUTRAL_400 = "#c0b6a5"
_ACCENT_200 = "#ffe1d0"
_ACCENT, _ACCENT_600, _ACCENT_700 = "#c67139", "#b2622d", "#8c491a"
_ERROR_BORDER = "#d8412f"
_ERROR_TEXT = "#b3301f"

MAX_LENGTH = 24  # EAN-13 / UPC fit easily; SKU-style codes typed on a keyboard too
_PLACEHOLDER = "0000 0000 0000 0"
_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "⌫"]

X_ICON = '<path d="M18 6 6 18"></path><path d="m6 6 12 12"></path>'
PLUS_ICON = '<path d="M5 12h14"></path><path d="M12 5v14"></path>'


def grouped(code: str) -> str:
    """'8691234510531' -> '8691 2345 1053 1' (the design groups digits by four)."""
    return " ".join(code[i:i + 4] for i in range(0, len(code), 4))


class _ShakeHolder(QWidget):
    """Holds the code field outside any layout so it can be nudged sideways (the error shake)."""

    def __init__(self, child: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self._child = child
        child.setParent(self)
        self._offset = 0
        self.setFixedHeight(child.height())

    def set_offset(self, offset: int) -> None:
        self._offset = offset
        self._child.setGeometry(offset, 0, self.width(), self.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.set_offset(self._offset)


class BarcodeKeypad(QWidget):
    """Full-window overlay; emits `submitted(code)` on "Add to sale" / Enter."""

    submitted = Signal(str)
    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("barcodeKeypad")
        self.setFocusPolicy(Qt.StrongFocus)
        self._code = ""
        self._error = ""
        self._shake: QVariantAnimation | None = None
        self.key_buttons: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        self._card = self._build_card()
        row.addWidget(self._card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self._refresh()
        self.hide()

    # --- building ------------------------------------------------------------

    def _build_card(self) -> QFrame:
        p = ORGANIC_PALETTE
        card = QFrame()
        card.setObjectName("keypadCard")
        card.setFixedWidth(560)
        card.setStyleSheet(f"#keypadCard {{ background-color: {p['background']}; border-radius: 36px; }}")
        soft_shadow(card, "lg")
        col = QVBoxLayout(card)
        col.setContentsMargins(28, 24, 28, 28)
        col.setSpacing(16)

        # Title row: eyebrow + heading, round close button.
        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        eyebrow = QLabel(upper(tr("pos.keypad.eyebrow")))
        eyebrow.setStyleSheet(f"font-size: 14px; font-weight: 800; letter-spacing: 1.4px; color: {p['text_secondary']};")
        heading = QLabel(tr("pos.keypad.title"))
        heading.setStyleSheet(f"font-family: {FONT_HEADING_CSS}; font-size: 30px; color: {p['text_primary']};")
        titles.addWidget(eyebrow)
        titles.addWidget(heading)
        title_row.addLayout(titles, stretch=1)
        self.close_button = QPushButton()
        self.close_button.setObjectName("keypadClose")
        self.close_button.setFixedSize(52, 52)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setToolTip(tr("pos.keypad.close"))
        self.close_button.setIcon(icons.icon(X_ICON, p["text_primary"], 22))
        self.close_button.setStyleSheet(
            f"#keypadClose {{ border: none; border-radius: 26px; background: {p['surface']}; }}"
            f"#keypadClose:hover {{ background: {p['border']}; }}")
        self.close_button.clicked.connect(self.close_keypad)
        title_row.addWidget(self.close_button, alignment=Qt.AlignTop)
        col.addLayout(title_row)

        # The code field: big digits grouped by four, "n / 13" on the right.
        self._field = QFrame()
        self._field.setObjectName("keypadField")
        self._field.setFixedHeight(84)
        field_row = QHBoxLayout(self._field)
        field_row.setContentsMargins(26, 0, 26, 0)
        field_row.setSpacing(12)
        self.code_label = QLabel()
        field_row.addWidget(self.code_label, stretch=1)
        self.length_label = QLabel()
        self.length_label.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {p['text_secondary']}; background: transparent;")
        field_row.addWidget(self.length_label)
        self._field_holder = _ShakeHolder(self._field)
        col.addWidget(self._field_holder)

        self.message_label = QLabel()
        self.message_label.setFixedHeight(20)
        col.addWidget(self.message_label)

        # 3 x 4 keypad.
        grid = QGridLayout()
        grid.setSpacing(12)
        for index, key in enumerate(_KEYS):
            button = self._key_button(key)
            self.key_buttons[key] = button
            grid.addWidget(button, index // 3, index % 3)
        col.addLayout(grid)

        self.submit_button = PillButton(tr("pos.keypad.submit"), _ACCENT, _ACCENT_600, _ACCENT_700, "white",
                                        height=84, font_px=21, icon=icons.icon(PLUS_ICON, "white", 24, 3))
        self.submit_button.setFocusPolicy(Qt.NoFocus)
        soft_shadow(self.submit_button, "md")
        self.submit_button.clicked.connect(self.submit)
        col.addWidget(self.submit_button)
        return card

    def _key_button(self, key: str) -> QPushButton:
        p = ORGANIC_PALETTE
        digit = key.isdigit()
        button = QPushButton(key)
        button.setObjectName("keypadKey")
        button.setFixedHeight(78)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        colour = _ERROR_TEXT if key == "C" else p["text_primary"]
        button.setStyleSheet(
            f"#keypadKey {{ border: none; border-radius: 39px; background: {_NEUTRAL_100 if digit else p['surface']}; "
            f"color: {colour}; font-family: {FONT_HEADING_CSS}; font-size: {34 if digit else 26}px; }}"
            f"#keypadKey:hover {{ background: {_NEUTRAL_200}; }}"
            f"#keypadKey:pressed {{ background: {_ACCENT_200}; }}")
        soft_shadow(button, "sm")
        if key == "C":
            button.setToolTip(tr("pos.keypad.clear"))
            button.clicked.connect(self.clear)
        elif key == "⌫":
            button.setToolTip(tr("pos.keypad.backspace"))
            button.clicked.connect(self.backspace)
        else:
            button.clicked.connect(lambda _c=False, d=key: self.type_text(d))
        return button

    # --- state ---------------------------------------------------------------

    def code(self) -> str:
        return self._code

    def error(self) -> str:
        return self._error

    def type_text(self, text: str) -> None:
        text = upper("".join(ch for ch in text if ch.isalnum() or ch in "-."))
        if not text:
            return
        self._code = (self._code + text)[:MAX_LENGTH]
        self._error = ""
        self._refresh()

    def backspace(self) -> None:
        self._code = self._code[:-1]
        self._error = ""
        self._refresh()

    def clear(self) -> None:
        self._code = ""
        self._error = ""
        self._refresh()

    def submit(self) -> None:
        if self._code:
            self.submitted.emit(self._code)

    def show_error(self, message: str) -> None:
        """The code was refused: red border + message, a short shake, the code stays for fixing."""
        self._error = message
        self._refresh()
        self._run_shake()

    def open_keypad(self) -> None:
        self._code = ""
        self._error = ""
        self._refresh()
        self._fit()
        self.show()
        self.raise_()
        self.setFocus(Qt.OtherFocusReason)
        fade_in(self, 180)

    def close_keypad(self) -> None:
        if self._shake is not None:
            self._shake.stop()
            self._field_holder.set_offset(0)
        was_open = self.isVisible()
        self.hide()
        self._code = ""
        self._error = ""
        self._refresh()
        if was_open:
            self.closed.emit()

    def _refresh(self) -> None:
        p = ORGANIC_PALETTE
        code = self._code
        self.code_label.setText(grouped(code) if code else _PLACEHOLDER)
        self.code_label.setStyleSheet(
            f"font-family: {FONT_HEADING_CSS}; font-size: 38px; letter-spacing: 2px; background: transparent; "
            f"color: {p['text_primary'] if code else _NEUTRAL_400};")
        self.length_label.setText(f"{len(code)} / 13")
        border = _ERROR_BORDER if self._error else "transparent"
        self._field.setStyleSheet(
            f"#keypadField {{ background-color: {_NEUTRAL_100}; border-radius: 24px; border: 3px solid {border}; }}")
        self.message_label.setText(self._error or tr("pos.keypad.hint"))
        self.message_label.setStyleSheet(
            f"font-size: 15px; font-weight: 700; color: {_ERROR_TEXT if self._error else p['text_secondary']};")
        self.submit_button.setEnabled(bool(code))

    def _run_shake(self) -> None:
        if not animations_enabled():
            return
        if self._shake is not None:
            self._shake.stop()
        shake = QVariantAnimation(self)
        shake.setDuration(350)
        # The design's @keyframes shake: 0 -> -10 -> 10 -> -10 -> 10 -> 0 px.
        for step, value in ((0.0, 0), (0.2, -10), (0.4, 10), (0.6, -10), (0.8, 10), (1.0, 0)):
            shake.setKeyValueAt(step, value)
        shake.setEasingCurve(QEasingCurve.Linear)
        shake.valueChanged.connect(lambda v: self._field_holder.set_offset(int(v)))
        self._shake = shake
        shake.start()

    # --- overlay plumbing ----------------------------------------------------

    def _fit(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(QRect(0, 0, parent.width(), parent.height()))

    def follow_parent(self) -> None:
        """The window was resized: keep covering all of it."""
        if self.isVisible():
            self._fit()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(46, 43, 37, int(255 * 0.72)))  # the design's 72% ink scrim

    def mousePressEvent(self, event) -> None:
        # A tap on the scrim (outside the card) closes, as in the design.
        if not self._card.geometry().contains(event.position().toPoint()):
            self.close_keypad()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.close_keypad()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.submit()
        elif key == Qt.Key_Backspace:
            self.backspace()
        elif key == Qt.Key_Delete:
            self.clear()
        elif event.text():
            self.type_text(event.text())
        else:
            super().keyPressEvent(event)
