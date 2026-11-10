"""'First steps' on the Overview of a brand-new installation.

Right after the root user is created every page is empty and nothing says where to begin. This card
lists the four things a shop needs before the first sale - a depot, a dealership, products, and accounts
for the people who will sign in - each with a button that opens the page where it is done. A step is
ticked as soon as its data exists, and the card disappears for good once all four are. Root only:
the others cannot create accounts, and by the time they sign in the set-up is done."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.theme import CLASSICAL_PALETTE
from shared.i18n import tr

# (step, the page its button opens)
STEPS = (("warehouse", "warehouses"), ("dealership", "dealerships"), ("product", "inventory"), ("accounts", "settings"))


class FirstStepsCard(Section):
    go = Signal(str)  # a page key: MainWindow navigates there

    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("admin.first.kicker"), tr("admin.first.heading"), parent)
        p = CLASSICAL_PALETTE
        self._marks: dict[str, QLabel] = {}
        self._texts: dict[str, QLabel] = {}
        self.buttons: dict[str, CompactButton] = {}
        for number, (step, page) in enumerate(STEPS, start=1):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(16, 8, 16, 8)
            line.setSpacing(12)
            mark = QLabel(str(number))
            mark.setFixedWidth(22)
            text = QLabel(tr(f"admin.first.{step}"))
            text.setWordWrap(True)
            button = CompactButton(tr(f"admin.first.{step}_go"), variant="primary")
            button.clicked.connect(lambda _checked=False, key=page: self.go.emit(key))
            line.addWidget(mark)
            line.addWidget(text, stretch=1)
            line.addWidget(button)
            self.body_layout().addWidget(row)
            self._marks[step], self._texts[step], self.buttons[step] = mark, text, button
        self._palette = p
        self.hide()

    def set_done(self, done: dict[str, bool] | None) -> None:
        """`done`: step -> whether it already exists. None (not root, or the data could not be read) or
        everything done hides the card."""
        if not done or all(done.get(step) for step, _ in STEPS):
            self.hide()
            return
        p = self._palette
        for number, (step, _page) in enumerate(STEPS, start=1):
            finished = bool(done.get(step))
            self._marks[step].setText("✓" if finished else str(number))
            self._marks[step].setStyleSheet(
                f"font-size: 15px; font-weight: 600; color: {p['alert_success'] if finished else p['accent']};")
            self._texts[step].setStyleSheet(
                f"font-size: 14px; color: {p['text_secondary'] if finished else p['text_primary']};")
            self.buttons[step].setVisible(not finished)
        self.show()
