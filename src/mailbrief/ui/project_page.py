"""Canonical project context; record rows only dispatch existing owner records."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from mailbrief.domain.actions import Action
from mailbrief.domain.drafts import DraftKind, DraftSummary
from mailbrief.domain.projects import Project
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList


class ProjectPage(QWidget):
    manage_requested = Signal()
    task_requested = Signal(object)
    note_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("projectPage")
        self.setAccessibleName("Projects")
        self._tasks: tuple[Action, ...] = ()
        self._notes: tuple[DraftSummary, ...] = ()
        self._projects: tuple[Project, ...] = ()
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.project = QComboBox()
        self.project.setObjectName("workspaceProject")
        self.project.setAccessibleName("Project")
        self.project.setMinimumContentsLength(16)
        self.project.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        row.addWidget(self.project, 1)
        self.manage = outline_button("Manage projects and areas", "manageWorkspaceProjects")
        self.manage.setAccessibleName("Manage projects and life areas")
        self.manage.clicked.connect(self.manage_requested)
        row.addWidget(self.manage)
        layout.addLayout(row)
        self.description = wrap_label("")
        self.description.setObjectName("workspaceProjectDescription")
        layout.addWidget(self.description)
        layout.addWidget(wrap_label("Tasks"))
        self.tasks = ActivatingList()
        self.tasks.setObjectName("projectTasks")
        self.tasks.setAccessibleName("Project tasks")
        layout.addWidget(self.tasks, 1)
        layout.addWidget(wrap_label("Notes"))
        self.notes = ActivatingList()
        self.notes.setObjectName("projectNotes")
        self.notes.setAccessibleName("Project notes")
        layout.addWidget(self.notes, 1)
        self.project.currentIndexChanged.connect(self._fill)
        self.tasks.itemActivated.connect(lambda _item: self._open_task())
        self.notes.itemActivated.connect(lambda _item: self._open_note())
        self._shown_tasks: tuple[Action, ...] = ()
        self._shown_notes: tuple[DraftSummary, ...] = ()

    def show_records(
        self,
        projects: tuple[Project, ...],
        tasks: tuple[Action, ...],
        notes: tuple[DraftSummary, ...],
    ) -> None:
        chosen = self.project.currentData()
        self._projects, self._tasks, self._notes = projects, tasks, notes
        self.project.blockSignals(True)
        self.project.clear()
        for project in projects:
            suffix = " (archived)" if project.archived_at_utc else ""
            self.project.addItem(project.name + suffix, project.public_id)
        self.project.setCurrentIndex(max(0, self.project.findData(chosen)))
        self.project.blockSignals(False)
        self._fill()

    def select_project(self, public_id: str) -> bool:
        index = self.project.findData(public_id)
        if index < 0:
            return False
        self.project.setCurrentIndex(index)
        return True

    def _fill(self) -> None:
        key = self.project.currentData()
        project = next((p for p in self._projects if p.public_id == key), None)
        self.description.setText(
            "Create a project to organize your work." if project is None else project.notes
        )
        self._shown_tasks = tuple(
            t for t in self._tasks if t.project and t.project.public_id == key
        )
        task_ids = {t.public_id for t in self._shown_tasks}
        self._shown_notes = tuple(
            n
            for n in self._notes
            if n.kind is DraftKind.NOTE
            and (key and n.project_public_id == key or n.action_public_id in task_ids)
        )
        self.tasks.clear()
        self.notes.clear()
        for task in self._shown_tasks:
            self.tasks.addItem(f"{task.title} · {task.status.value}")
        for note in self._shown_notes:
            self.notes.addItem(note.display_title)

    def _open_task(self) -> None:
        index = self.tasks.currentRow()
        if 0 <= index < len(self._shown_tasks):
            self.task_requested.emit(self._shown_tasks[index])

    def _open_note(self) -> None:
        index = self.notes.currentRow()
        if 0 <= index < len(self._shown_notes):
            self.note_requested.emit(self._shown_notes[index])
