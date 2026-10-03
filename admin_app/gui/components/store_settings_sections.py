"""The three store-wide blocks at the top of Admin > Settings:
General (store name/address on receipts and reports), Notifications
(which alerts are on) and Data location (where the shared database
lives). Each is a Section with its own Save; the page only stacks them.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QWidget,
)

from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.theme import CLASSICAL_PALETTE
from database import connection, settings_repository
from database.exceptions import DATABASE_ERRORS
from shared import paths
from shared import store_settings as ss
from shared.constants import DATABASE_FILENAME

_INPUT_CSS = (
    f"background: {CLASSICAL_PALETTE['surface']}; color: {CLASSICAL_PALETTE['text_primary']};"
    f"border: 1px solid {CLASSICAL_PALETTE['border']}; border-radius: 6px; padding: 6px 8px; font-size: 13px;"
)


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size: 12px; color: {CLASSICAL_PALETTE['text_secondary']};")
    return label


def _form_host(section: Section) -> QFormLayout:
    host = QWidget()
    host.setStyleSheet("background: transparent;")
    form = QFormLayout(host)
    form.setContentsMargins(16, 12, 16, 12)
    section.body_layout().addWidget(host)
    return form


class GeneralSection(Section):
    saved = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Store", "General", parent)
        self._name_input = QLineEdit()
        self._name_input.setStyleSheet(_INPUT_CSS)
        self._name_input.setMaxLength(ss.MAX_NAME_LENGTH)
        self._address_input = QPlainTextEdit()
        self._address_input.setStyleSheet(_INPUT_CSS)
        self._address_input.setFixedHeight(84)
        self._address_input.setPlaceholderText("One line per row, up to 4 (street, city, phone ...)")
        form = _form_host(self)
        form.addRow("Store name", self._name_input)
        form.addRow("Address", self._address_input)
        form.addRow(_note("Printed at the top of every till receipt and every exported report."))
        self._error = QLabel("")
        self._error.setStyleSheet(f"font-size: 12px; color: {CLASSICAL_PALETTE['alert_critical']};")
        form.addRow(self._error)
        save = CompactButton("Save")
        save.clicked.connect(self.save)
        self.add_header_control(save)
        self.reload()

    def reload(self) -> None:
        profile = settings_repository.safe_store_profile()
        self._name_input.setText(profile.name)
        self._address_input.setPlainText("\n".join(profile.address_lines))

    def save(self) -> bool:
        try:
            profile = ss.validate_profile(self._name_input.text(), self._address_input.toPlainText())
            settings_repository.save_store_profile(profile)
        except (ValueError, DATABASE_ERRORS) as exc:
            self._error.setText(str(exc))
            return False
        self._error.setText("")
        self._name_input.setText(profile.name)
        self.saved.emit()
        return True


class NotificationsSection(Section):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Alerts", "Notifications", parent)
        self._low_stock = QCheckBox("Show the low-stock alert banner on the depot floor")
        self._pending = QCheckBox("Show the pending-approvals count on Purchase requests and page headers")
        for box in (self._low_stock, self._pending):
            box.setStyleSheet(f"color: {CLASSICAL_PALETTE['text_primary']}; font-size: 13px; padding: 4px 0;")
        form = _form_host(self)
        form.addRow(self._low_stock)
        form.addRow(self._pending)
        form.addRow(_note("Turning an alert off only hides it; purchase requests and stock levels are unaffected."))
        save = CompactButton("Save")
        save.clicked.connect(self.save)
        self.add_header_control(save)
        self.reload()

    def reload(self) -> None:
        prefs = settings_repository.safe_notifications()
        self._low_stock.setChecked(prefs.low_stock_alerts)
        self._pending.setChecked(prefs.pending_approvals)

    def save(self) -> bool:
        prefs = ss.NotificationPrefs(self._low_stock.isChecked(), self._pending.isChecked())
        try:
            settings_repository.save_notifications(prefs)
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return False
        self.changed.emit()
        return True


class DataLocationSection(Section):
    def __init__(self, parent: QWidget | None = None):
        super().__init__("Storage", "Data location", parent)
        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setStyleSheet(f"font-size: 13px; color: {CLASSICAL_PALETTE['text_primary']};")
        form = _form_host(self)
        form.addRow(self._path_label)
        form.addRow(_note(
            "All apps read and write this one database file. To move it, pick a folder: a copy is made there "
            "and every app uses it after its next restart. The old file is left untouched."
        ))
        change = CompactButton("Change folder...")
        change.clicked.connect(self.choose_folder)
        self.add_header_control(change)
        self.reload()

    def reload(self) -> None:
        self._path_label.setText(str(paths.get_db_path()))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the new data folder", str(paths.get_db_path().parent))
        if folder:
            self.move_to(Path(folder))

    def move_to(self, folder: Path) -> bool:
        target = folder / DATABASE_FILENAME
        if target.resolve() == paths.get_db_path().resolve():
            return False
        try:
            if target.exists():
                if QMessageBox.question(
                    self, "Database already there",
                    f"{target} already holds a database. Use that one instead of copying the current data?",
                ) != QMessageBox.Yes:
                    return False
            else:
                connection.copy_database_to(target)
            paths.set_db_path(target)
        except (OSError, DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, "Couldn't move the data", str(exc))
            return False
        self.reload()
        QMessageBox.information(
            self, "Restart the apps",
            "The new location is saved. Close and reopen Admin, depot and POS on every computer so they all use it.",
        )
        return True
