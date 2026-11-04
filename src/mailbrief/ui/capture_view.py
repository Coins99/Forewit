"""Explicit, account-free capture. No record exists until Save succeeds."""

from uuid import uuid4

from pydantic import ValidationError
from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import Action
from mailbrief.domain.capture import CaptureRequest
from mailbrief.domain.drafts import Draft
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.labels import outline_button, wrap_label


class CaptureDialog(QDialog):
    save_requested = Signal(object)
    open_requested = Signal(object)
    today_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("captureDialog")
        self.setAccessibleName("Capture task or note")
        self.setWindowTitle("Capture")
        self.resize(460, 330)
        self._saving = False
        self._attempted = False
        self._command = str(uuid4())
        self._saved: Action | Draft | None = None
        layout = QVBoxLayout(self)
        dialog_heading(
            self, layout, "Capture", "Save a task or note. Organize it when you need to."
        )
        self.kind = QComboBox()
        self.kind.setObjectName("captureKind")
        self.kind.setAccessibleName("Capture type")
        self.kind.addItems(["Task", "Note"])
        layout.addWidget(self.kind)
        self.title = QLineEdit()
        self.title.setObjectName("captureTitle")
        self.title.setAccessibleName("Title")
        self.title.setPlaceholderText("What do you want to remember?")
        self.title.setMaxLength(200)
        layout.addWidget(self.title)
        self.details = outline_button("Add notes", "captureDetails")
        layout.addWidget(self.details)
        self.body = QPlainTextEdit()
        self.body.setObjectName("captureBody")
        self.body.setAccessibleName("Note text or task notes")
        self.body.setPlaceholderText("Your text")
        self.body.setTabChangesFocus(True)
        layout.addWidget(self.body, 1)
        self.status = wrap_label("")
        self.status.setObjectName("captureStatus")
        self.status.setAccessibleName("Capture save status")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.save_button = outline_button("Save", "captureSave", primary=True)
        self.open_button = outline_button("Open", "captureOpen")
        self.new_button = outline_button("Capture another", "captureAnother")
        self.discard_button = outline_button("Discard and close", "captureDiscard")
        self.close_button = outline_button("Cancel", "captureClose")
        for button in (
            self.save_button,
            self.open_button,
            self.new_button,
            self.discard_button,
            self.close_button,
            self.details,
        ):
            button.setAccessibleName(button.text())
        for button in (
            self.save_button,
            self.open_button,
            self.new_button,
            self.discard_button,
            self.close_button,
        ):
            row.addWidget(button)
        layout.addLayout(row)
        self.today_button = outline_button("Add to today", "captureAddToday")
        self.today_button.setAccessibleName("Add saved task to today's focus")
        layout.addWidget(self.today_button)
        self.today_button.clicked.connect(self._add_today)
        self.save_button.clicked.connect(self._submit)
        self.title.returnPressed.connect(self._submit)
        self.close_button.clicked.connect(self.reject)
        self.discard_button.clicked.connect(self.force_close)
        self.new_button.clicked.connect(self.open_fresh)
        self.open_button.clicked.connect(self._open_saved)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.details.clicked.connect(lambda: self.body.setVisible(not self.body.isVisible()))
        self.open_fresh()

    def open_fresh(self) -> None:
        if self._saving or self.has_unsaved_input():
            return
        self._command = str(uuid4())
        self._attempted = False
        self._saved = None
        self.title.clear()
        self.body.clear()
        self.kind.setCurrentIndex(0)
        self.body.hide()
        self.details.show()
        self.status.clear()
        self.discard_button.hide()
        self.close_button.setText("Cancel")
        self._controls()
        self.title.setFocus()

    def _kind_changed(self) -> None:
        note = self.kind.currentIndex() == 1
        self.body.setVisible(note or bool(self.body.toPlainText()))
        self.details.setVisible(not note)

    def _submit(self) -> None:
        if self._saving or self._saved is not None:
            return
        try:
            request = CaptureRequest(
                command_id=self._command,
                kind="task" if self.kind.currentIndex() == 0 else "note",
                title=self.title.text(),
                body=self.body.toPlainText(),
            )
        except ValidationError:
            self.status.setText(
                "A task needs a title and up to 10,000 characters of notes."
                if self.kind.currentIndex() == 0
                else "Enter a title or text, up to 20,000 characters."
            )
            return
        self._attempted = True
        self.set_saving(True)
        self.save_requested.emit(request)

    def _controls(self) -> None:
        saved = self._saved is not None
        self.kind.setEnabled(not (self._saving or self._attempted or saved))
        self.title.setEnabled(not (self._saving or saved))
        self.body.setEnabled(not (self._saving or saved))
        self.save_button.setEnabled(not self._saving)
        self.save_button.setVisible(not saved)
        self.open_button.setVisible(saved)
        self.today_button.setVisible(isinstance(self._saved, Action))
        self.new_button.setVisible(saved)
        self.close_button.setEnabled(not self._saving)
        self.discard_button.setEnabled(not self._saving)

    def set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._controls()

    def failed(self, message: str) -> None:
        self.set_saving(False)
        self.status.setText(message)

    def saved(self, result: Action | Draft) -> None:
        self._saved = result
        self._saving = False
        self.discard_button.hide()
        self.close_button.setText("Done")
        self.status.setText("Task saved." if isinstance(result, Action) else "Note saved.")
        self._controls()

    def _open_saved(self) -> None:
        if self._saved is not None:
            self.open_requested.emit(self._saved)

    def _add_today(self) -> None:
        if isinstance(self._saved, Action):
            self.today_requested.emit(self._saved)

    def has_unsaved_input(self) -> bool:
        return self._saved is None and bool(self.title.text() or self.body.toPlainText())

    def reject(self) -> None:
        if self._saving:
            return
        if self.has_unsaved_input():
            self.status.setText("Save your capture, or choose Discard and close.")
            self.discard_button.show()
            return
        super().reject()

    def force_close(self) -> None:
        if not self._saving:
            self.title.clear()
            self.body.clear()
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.reject()
