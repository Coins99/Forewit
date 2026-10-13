"""Nonblocking settings editor; secrets are never loaded back into the form."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mailbrief.providers.groq.credentials import GroqKeyError, parse_api_key
from mailbrief.ui.appearance_view import AppearancePanel
from mailbrief.ui.dialog_guard import DialogGuard
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.preferences_view import PreferencesPanel


class SettingsDialog(QDialog):
    """Three tabs: this device's connection and AI settings, the owner's preferences,
    which are saved in the database and shared with the CLI, and this device's colours."""

    save_requested = Signal(object)
    key_requested = Signal(object)
    remove_key_requested = Signal()
    revoke_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # Its pickers and boxes open behind this cover, never modal, so quitting works.
        self.dialog_guard = DialogGuard(self)
        self.setWindowTitle("MailBrief settings")
        self.resize(640, 620)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        connection = QWidget()
        connection_layout = QVBoxLayout(connection)
        self.fields = QWidget()
        form = QFormLayout(self.fields)
        self.oauth_path = QLineEdit()
        self.oauth_path.setObjectName("customGoogleOAuthPath")
        self.oauth_path.setPlaceholderText("Optional: leave blank to use MailBrief Google sign-in")
        self.oauth_path.setAccessibleName("Google Desktop OAuth client file")
        self.browse_button = QPushButton("&Browse…")
        path_row = QHBoxLayout()
        path_row.addWidget(self.oauth_path, 1)
        path_row.addWidget(self.browse_button)
        form.addRow("Custom Google client (optional)", path_row)
        self.model = QLineEdit()
        self.model.setMaxLength(128)
        self.model.setPlaceholderText("Choose a model, e.g. openai/gpt-oss-120b")
        self.model.setAccessibleName("Groq Structured Outputs model")
        form.addRow("Groq model", self.model)
        self.save_button = QPushButton("&Save settings")
        form.addRow(self.save_button)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(512)
        self.key.setAccessibleName("New Groq API key")
        self.key.setPlaceholderText("Enter a new key; existing keys are never displayed")
        form.addRow("Groq API key", self.key)
        self.key_button = QPushButton("Save key in &OS credential store")
        self.remove_key_button = QPushButton("&Remove stored Groq key")
        form.addRow(self.key_button)
        form.addRow(self.remove_key_button)
        self.revoke_button = QPushButton("Revoke AI consent (briefs and &drafting)")
        form.addRow(self.revoke_button)
        connection_layout.addWidget(self.fields)
        notice = QLabel(
            "Sign in with Google using MailBrief's configuration. A custom Desktop OAuth "
            "client file is optional for development builds. Choose a Groq model that supports "
            "Structured Outputs. Enable Zero Data Retention in Groq Console before sending mail. "
            "Only the file location and model are saved in desktop settings. "
            "Removing the key or revoking consent keeps saved briefs and cached analyses."
        )
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        connection_layout.addWidget(notice)
        connection_layout.addStretch(1)
        self.tabs.addTab(connection, "Co&nnection and AI")
        self.preferences_panel = PreferencesPanel()
        self.tabs.addTab(self.preferences_panel, "Pre&ferences")
        self.appearance_panel = AppearancePanel()
        self.tabs.addTab(self.appearance_panel, "&Appearance")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.close_button = QPushButton("&Close")
        layout.addWidget(self.close_button)
        self.close_button.clicked.connect(self.reject)
        self.browse_button.clicked.connect(self._browse)
        self.save_button.clicked.connect(self._save)
        self.key_button.clicked.connect(self._save_key)
        self.remove_key_button.clicked.connect(self.remove_key_requested.emit)
        self.revoke_button.clicked.connect(self.revoke_requested.emit)
        self.preferences_panel.status_changed.connect(self.status.setText)
        self._picker: QFileDialog | None = None
        self._loaded_input = self._input()

    def _input(self) -> tuple[str, ...]:
        """The connection fields and a typed key, to tell whether any of it is unsaved."""
        return (self.oauth_path.text(), self.model.text(), self.key.text())

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something typed here or in Preferences."""
        return self._input() != self._loaded_input or self.preferences_panel.has_unsaved_input()

    def set_preferences(self, preferences: DesktopPreferences) -> None:
        self.oauth_path.setText(str(preferences.gmail_oauth_client_path or ""))
        self.model.setText(preferences.groq_model or "")
        self.key.clear()
        self.status.clear()
        self._loaded_input = self._input()

    def set_busy(self, busy: bool) -> None:
        self.fields.setEnabled(not busy)
        self.preferences_panel.setEnabled(not busy)
        if busy:
            self.status.setText("Applying changes…")

    def _browse(self) -> None:
        self._picker = QFileDialog(self, "Choose Google Desktop OAuth client")
        self._picker.setFileMode(QFileDialog.FileMode.ExistingFile)
        self._picker.setNameFilter("JSON files (*.json)")
        self._picker.fileSelected.connect(self.oauth_path.setText)
        self.dialog_guard.show_dialog(self._picker)

    def _save(self) -> None:
        raw_path = self.oauth_path.text().strip()
        preferences = DesktopPreferences(
            gmail_oauth_client_path=Path(raw_path).expanduser() if raw_path else None,
            groq_model=self.model.text().strip() or None,
        )
        self.save_requested.emit(preferences)
        self._loaded_input = self._input()  # Asked to save: no longer unsaved.

    def _save_key(self) -> None:
        raw = self.key.text()
        self.key.clear()
        try:
            key = parse_api_key(raw)
        except GroqKeyError:
            self.status.setText("The key is invalid. Nothing was saved.")
            return
        self.key_requested.emit(key)

    def reject(self) -> None:
        self.key.clear()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.key.clear()
        super().closeEvent(event)
