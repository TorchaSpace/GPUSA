"""The three store-wide blocks at the top of Admin > Settings:
General (store name/address on receipts and reports), Notifications
(which alerts are on) and Data location (where the shared database
lives). Each is a Section with its own Save; the page only stacks them.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QWidget,
)

from admin_app.gui.components.animated_checkbox import AnimatedCheckBox
from admin_app.gui.motion import toast
from admin_app.gui.components.compact_button import CompactButton
from admin_app.gui.components.section import Section
from admin_app.theme import CLASSICAL_PALETTE
from database import connection, settings_repository
from database.exceptions import DATABASE_ERRORS
from shared import i18n, paths
from shared import store_settings as ss
from shared.constants import DATABASE_FILENAME
from shared.i18n import tr

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
        super().__init__(tr("settings.store.kicker"), tr("settings.general"), parent)
        self._name_input = QLineEdit()
        self._name_input.setStyleSheet(_INPUT_CSS)
        self._name_input.setMaxLength(ss.MAX_NAME_LENGTH)
        self._address_input = QPlainTextEdit()
        self._address_input.setStyleSheet(_INPUT_CSS)
        self._address_input.setFixedHeight(84)
        self._address_input.setPlaceholderText(tr("settings.address_hint"))
        form = _form_host(self)
        form.addRow(tr("settings.store_name"), self._name_input)
        form.addRow(tr("settings.address"), self._address_input)
        self._language_input = QComboBox()
        for code, name in i18n.available_languages():
            self._language_input.addItem(name, code)
        form.addRow(tr("settings.language"), self._language_input)
        form.addRow(_note(tr("settings.store_note")))
        form.addRow(_note(tr("settings.language_note")))
        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"font-size: 12px; color: {CLASSICAL_PALETTE['alert_critical']};")
        form.addRow(self._error)
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: 12px; color: {CLASSICAL_PALETTE['alert_success']};")
        form.addRow(self._status)
        save = CompactButton(tr("common.save"), variant="primary")
        save.clicked.connect(self.save)
        self.add_header_control(save)
        self._baseline: tuple[str, str, object] = ("", "", None)
        self.reload()

    def _current(self) -> tuple[str, str, object]:
        return (self._name_input.text(), self._address_input.toPlainText(), self._language_input.currentData())

    def is_dirty(self) -> bool:
        """True when the fields differ from what was last loaded or saved
        (unsaved edits)."""
        return self._current() != self._baseline

    def reload(self) -> None:
        profile = settings_repository.safe_store_profile()
        self._name_input.setText(profile.name)
        self._address_input.setPlainText("\n".join(profile.address_lines))
        index = self._language_input.findData(settings_repository.safe_language())
        self._language_input.setCurrentIndex(max(0, index))
        self._error.setText("")
        self._status.setText("")
        self._baseline = self._current()

    def save(self) -> bool:
        try:
            profile = ss.validate_profile(self._name_input.text(), self._address_input.toPlainText())
            # Name, address and language in ONE transaction: all saved or none.
            settings_repository.save_profile_and_language(profile, self._language_input.currentData())
        except (ValueError, *DATABASE_ERRORS) as exc:
            self._error.setText(str(exc))
            return False
        self._error.setText("")
        self._status.setText(tr("settings.saved"))
        toast(self, tr("settings.saved"))  # visible proof the click did something
        self._name_input.setText(profile.name)
        self._baseline = self._current()
        self.saved.emit()
        if self._language_input.currentData() != i18n.current_language():
            self._offer_restart()
        return True

    def _confirm_restart(self) -> bool:
        """Separate so tests can answer without a modal dialog."""
        box = QMessageBox(self)
        box.setWindowTitle(tr("settings.language_restart_title"))
        box.setText(tr("settings.language_restart_body"))
        now = box.addButton(tr("settings.language_restart_now"), QMessageBox.AcceptRole)
        box.addButton(tr("settings.language_restart_later"), QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is now

    def _offer_restart(self) -> None:
        if self._confirm_restart():
            from admin_app.gui.restart import restart_app

            restart_app()


class NotificationsSection(Section):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("settings.alerts.kicker"), tr("settings.notifications"), parent)
        self._low_stock = AnimatedCheckBox(tr("settings.low_stock_alerts"))
        self._pending = AnimatedCheckBox(tr("settings.pending_badge"))
        for box in (self._low_stock, self._pending):
            box.setStyleSheet(f"color: {CLASSICAL_PALETTE['text_primary']}; font-size: 13px; padding: 4px 0;")
        form = _form_host(self)
        form.addRow(self._low_stock)
        form.addRow(self._pending)
        form.addRow(_note(tr("settings.notifications_note")))
        save = CompactButton(tr("common.save"), variant="primary")
        save.clicked.connect(self.save)
        self.add_header_control(save)
        self._baseline: tuple[bool, bool] = (True, True)
        self.reload()

    def _current(self) -> tuple[bool, bool]:
        return (self._low_stock.isChecked(), self._pending.isChecked())

    def is_dirty(self) -> bool:
        """True when a switch differs from what was last loaded or saved."""
        return self._current() != self._baseline

    def reload(self) -> None:
        prefs = settings_repository.safe_notifications()
        self._low_stock.setChecked(prefs.low_stock_alerts)
        self._pending.setChecked(prefs.pending_approvals)
        self._baseline = self._current()

    def save(self) -> bool:
        prefs = ss.NotificationPrefs(self._low_stock.isChecked(), self._pending.isChecked())
        try:
            settings_repository.save_notifications(prefs)
        except DATABASE_ERRORS as exc:
            QMessageBox.warning(self, tr("admin.settings.save_failed"), str(exc))
            return False
        self._baseline = self._current()
        self.changed.emit()
        return True


class DataLocationSection(Section):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(tr("settings.storage.kicker"), tr("settings.data_location"), parent)
        self._path_label = QLabel()
        self._path_label.setWordWrap(True)
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setStyleSheet(f"font-size: 13px; color: {CLASSICAL_PALETTE['text_primary']};")
        form = _form_host(self)
        form.addRow(self._path_label)
        form.addRow(_note(tr("admin.settings.data_note")))
        change = CompactButton(tr("settings.change_folder"))
        change.clicked.connect(self.choose_folder)
        self.add_header_control(change)
        self.reload()

    def reload(self) -> None:
        self._path_label.setText(str(paths.get_db_path()))

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("admin.settings.choose_folder"), str(paths.get_db_path().parent))
        if folder:
            self.move_to(Path(folder))

    def move_to(self, folder: Path) -> bool:
        target = folder / DATABASE_FILENAME
        if target.resolve() == paths.get_db_path().resolve():
            return False
        try:
            if target.exists():
                if QMessageBox.question(
                    self, tr("admin.settings.db_there_title"),
                    tr("admin.settings.db_there_body").format(target=target),
                ) != QMessageBox.Yes:
                    return False
            else:
                connection.copy_database_to(target)
            paths.set_db_path(target)
        except (OSError, *DATABASE_ERRORS) as exc:
            QMessageBox.warning(self, tr("admin.settings.move_failed"), str(exc))
            return False
        self.reload()
        QMessageBox.information(
            self, tr("admin.settings.restart_title"), tr("admin.settings.restart_body"),
        )
        return True
