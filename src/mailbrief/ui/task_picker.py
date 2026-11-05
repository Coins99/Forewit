"""Choose the task a note belongs to (ADR 0027). The window searches; the picker only lists.

Task titles are the owner's text, so rows are plain list-item text and never tooltips.
"""

from typing import Final

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QListWidgetItem, QVBoxLayout, QWidget

from mailbrief.domain.actions import ActionStatus, TaskChoice
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list

SEARCH_DELAY_MS: Final = 200
_TASK_ID: Final = Qt.ItemDataRole.UserRole + 1


def choice_text(action: TaskChoice) -> str:
    """One plain line: the title, and whether the task is done or waits on someone."""
    if action.status is not ActionStatus.OPEN:
        return f"{action.title} · {action.status.value.capitalize()}"
    if action.ownership is ActionOwnership.WAITING_FOR:
        return f"{action.title} · Waiting"
    return action.title


class TaskPickerDialog(QDialog):
    """``search_requested(text)`` asks the window for matches, answered by show_choices()
    or show_failed(); ``chosen(public_id)`` is the task picked, after which it closes."""

    search_requested = Signal(str)
    chosen = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("taskPickerDialog")
        self.setAccessibleName("Link the note to a task")
        self.setWindowTitle("Link to task")
        self.resize(480, 460)
        layout = QVBoxLayout(self)
        dialog_heading(
            self,
            layout,
            "Link to task",
            "Choose the task this note is about. The task stays as it is.",
        )
        self.query = QLineEdit()
        self.query.setObjectName("taskPickerSearch")
        self.query.setAccessibleName("Search task titles")
        self.query.setPlaceholderText("Search task titles (two or more letters)")
        self.query.setMaxLength(200)
        layout.addWidget(self.query)
        self.choices = ActivatingList()
        self.choices.setObjectName("taskPickerList")
        self.choices.setAccessibleName("Up to 50 matching tasks, open ones first")
        layout.addWidget(self.choices, 1)
        self.status = wrap_label("Up to 50 tasks, open ones first.", tone="muted")
        self.status.setObjectName("taskPickerStatus")
        self.status.setAccessibleName("Task search status")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.link_button = outline_button("Link", "taskPickerLink", primary=True)
        self.cancel_button = outline_button("Cancel", "taskPickerCancel")
        for button in (self.link_button, self.cancel_button):
            button.setAccessibleName(button.text())
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DELAY_MS)
        self._timer.timeout.connect(lambda: self.search_requested.emit(self.query.text()))
        self.query.textChanged.connect(self._typed)
        self.choices.itemActivated.connect(lambda _item: self._link())
        self.link_button.clicked.connect(self._link)
        self.cancel_button.clicked.connect(self.reject)
        self.link_button.setEnabled(False)

    def open_fresh(self) -> None:
        """Start again with no words, and ask for the most recent tasks."""
        self.query.blockSignals(True)
        self.query.clear()
        self.query.blockSignals(False)
        clear_list(self.choices)
        self.link_button.setEnabled(False)
        self.status.setText("Up to 50 tasks, open ones first.")
        self.query.setFocus()
        self.search_requested.emit("")

    def show_choices(self, actions: tuple[TaskChoice, ...]) -> None:
        clear_list(self.choices)
        for action in actions:
            item = QListWidgetItem(choice_text(action))
            item.setData(_TASK_ID, action.public_id)
            self.choices.addItem(item)
        if actions:
            self.choices.setCurrentRow(0)
        self.link_button.setEnabled(bool(actions))
        self.status.setText(
            "Up to 50 tasks, open ones first."
            if actions
            else "No tasks match. Try other words, or two or more letters."
        )

    def show_failed(self) -> None:
        self.status.setText("Tasks could not be loaded. Try again.")

    def _typed(self) -> None:
        clear_list(self.choices)
        self.link_button.setEnabled(False)
        self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _link(self) -> None:
        item = self.choices.currentItem()
        value = None if item is None else item.data(_TASK_ID)
        if isinstance(value, str):
            self.chosen.emit(value)
            self.accept()
