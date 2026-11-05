"""An explicit note-link edit. Text is saved before this dialog opens."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QVBoxLayout, QWidget

from mailbrief.domain.actions import Action
from mailbrief.domain.drafts import Draft
from mailbrief.domain.projects import Project
from mailbrief.ui.labels import outline_button


class NoteLinksDialog(QDialog):
    save_requested = Signal(object, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("noteLinksDialog")
        self.setAccessibleName("Note project and task links")
        self.setWindowTitle("Note links")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.project = QComboBox()
        self.project.setObjectName("noteProject")
        self.project.setAccessibleName("Note project")
        self.task = QComboBox()
        self.task.setObjectName("noteTask")
        self.task.setAccessibleName("Note task")
        for combo in (self.project, self.task):
            combo.setMinimumContentsLength(16)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
        form.addRow("Project", self.project)
        form.addRow("Task", self.task)
        layout.addLayout(form)
        self.save = outline_button("Save links", "saveNoteLinks", primary=True)
        self.save.setAccessibleName("Save note links")
        self.save.clicked.connect(
            lambda: self.save_requested.emit(self.project.currentData(), self.task.currentData())
        )
        layout.addWidget(self.save)

    def configure(
        self, note: Draft, projects: tuple[Project, ...], tasks: tuple[Action, ...]
    ) -> None:
        self.project.clear()
        self.task.clear()
        self.project.addItem("No project", None)
        self.task.addItem("No task", None)
        for project in projects:
            if project.archived_at_utc is None or project.public_id == note.project_public_id:
                self.project.addItem(project.name, project.public_id)
        for task in tasks:
            self.task.addItem(task.title, task.public_id)
        self.project.setCurrentIndex(max(0, self.project.findData(note.project_public_id)))
        self.task.setCurrentIndex(max(0, self.task.findData(note.action_public_id)))
