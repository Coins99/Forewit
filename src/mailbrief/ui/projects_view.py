"""Manage projects and life areas; owner writing only in plain-text controls."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.projects import AreaEdit, LifeArea, Project, ProjectEdit
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.labels import button_label, outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list


class ProjectsDialog(QDialog):
    save_requested = Signal(str, object, object, object)
    archive_requested = Signal(object, bool)
    delete_area_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Projects and life areas")
        self.setObjectName("projectsDialog")
        self.setAccessibleName("Projects and life areas")
        self.resize(650, 470)
        self.projects: tuple[Project, ...] = ()
        self.areas: tuple[LifeArea, ...] = ()
        self._selected: Project | LifeArea | None = None
        self._binding = False
        layout = QVBoxLayout(self)
        dialog_heading(self, layout, "Projects and life areas")
        self.kind = QComboBox()
        self.kind.setObjectName("organizationKind")
        self.kind.setAccessibleName("Projects or life areas")
        self.kind.addItems(["Projects", "Life areas"])
        layout.addWidget(self.kind)
        columns = QHBoxLayout()
        self.list = ActivatingList()
        self.list.setObjectName("organizationList")
        self.list.setAccessibleName("Projects and life areas list")
        columns.addWidget(self.list, 1)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.name.setObjectName("organizationName")
        self.name.setAccessibleName("Name")
        form.addRow("&Name", self.name)
        self.notes = QPlainTextEdit()
        self.notes.setTabChangesFocus(True)
        self.notes.setObjectName("organizationNotes")
        self.notes.setAccessibleName("Notes")
        form.addRow("No&tes", self.notes)
        self.area = QComboBox()
        self.area.setObjectName("projectArea")
        self.area.setAccessibleName("Life area for project")
        self.area.setMinimumContentsLength(14)
        self.area.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        form.addRow("Life &area", self.area)
        self.area_label = form.labelForField(self.area)
        if self.area_label is not None:
            self.area_label.setObjectName("projectAreaLabel")
            self.area_label.setAccessibleName("Life area")
        columns.addLayout(form, 2)
        layout.addLayout(columns)
        self.hint = wrap_label(
            "Archiving keeps a project's tasks. Removing an area keeps its projects."
        )
        self.hint.setObjectName("organizationHint")
        layout.addWidget(self.hint)
        self.status = wrap_label("")
        self.status.setObjectName("organizationStatus")
        self.status.setAccessibleName("Organization save status")
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.new = outline_button("New", "newOrganization")
        self.save = outline_button("Save", "saveOrganization", primary=True)
        self.discard = outline_button("Discard edits", "discardOrganizationEdits")
        self.archive = outline_button("Archive project", "archiveProject")
        self.delete_area = outline_button("Remove area", "removeArea")
        self.close_button = outline_button("Close", "closeOrganization")
        for button in (
            self.new,
            self.save,
            self.discard,
            self.archive,
            self.delete_area,
            self.close_button,
        ):
            button.setAccessibleName(button.text())
            row.addWidget(button)
        layout.addLayout(row)
        self.new.clicked.connect(self.new_record)
        self.kind.currentIndexChanged.connect(self._fill)
        self.list.currentRowChanged.connect(self._select)
        self.save.clicked.connect(self._save)
        self.archive.clicked.connect(self._archive)
        self.delete_area.clicked.connect(self._delete_area)
        self.close_button.clicked.connect(self.reject)
        self.discard.clicked.connect(self._discard)
        self.name.textChanged.connect(self._edited)
        self.notes.textChanged.connect(self._edited)
        self.area.currentIndexChanged.connect(self._edited)
        self._busy = False
        self._fill()

    def records(self) -> tuple[Project | LifeArea, ...]:
        return self.projects if self.kind.currentIndex() == 0 else self.areas

    def show_records(
        self,
        projects: tuple[Project, ...],
        areas: tuple[LifeArea, ...],
        selected_id: str | None = None,
    ) -> None:
        chosen = selected_id or (None if self._selected is None else self._selected.public_id)
        self.projects, self.areas = projects, areas
        self._fill()
        for index, record in enumerate(self.records()):
            if record.public_id == chosen:
                self.list.setCurrentRow(index)
                break

    def _fill(self) -> None:
        clear_list(self.list)
        for record in self.records():
            suffix = " (archived)" if isinstance(record, Project) and record.archived_at_utc else ""
            self.list.addItem(record.name + suffix)
        self._reset_record()

    def new_record(self) -> None:
        if not self._busy and not self.is_dirty():
            self._reset_record()

    def _reset_record(self) -> None:
        self._binding = True
        self._selected = None
        self.list.setCurrentRow(-1)
        self.name.clear()
        self.notes.clear()
        self.status.clear()
        self._fill_area(None)
        self._binding = False
        self._buttons()
        self.name.setFocus()

    def _fill_area(self, selected: str | None) -> None:
        self.area.clear()
        self.area.addItem("No life area", None)
        for area in self.areas:
            self.area.addItem(area.name, area.public_id)
        self.area.setCurrentIndex(max(0, self.area.findData(selected)))
        self.area.setVisible(self.kind.currentIndex() == 0)
        if self.area_label is not None:
            self.area_label.setVisible(self.kind.currentIndex() == 0)

    def _select(self, index: int) -> None:
        records = self.records()
        if not 0 <= index < len(records):
            return
        record = records[index]
        self._binding = True
        self._selected = record
        self.name.setText(record.name)
        self.notes.setPlainText(record.notes)
        self.status.clear()
        self._fill_area(record.area_public_id if isinstance(record, Project) else None)
        self._binding = False
        self._buttons()

    def _buttons(self) -> None:
        project = self.kind.currentIndex() == 0
        self.archive.setVisible(project)
        self.delete_area.setVisible(not project)
        archive_label = (
            "Reopen project"
            if isinstance(self._selected, Project) and self._selected.archived_at_utc
            else "Archive project"
        )
        self.archive.setText(button_label(archive_label))
        self.archive.setAccessibleName(archive_label)
        can_leave = not self._busy and not self.is_dirty()
        for widget in (self.kind, self.list, self.new):
            widget.setEnabled(can_leave)
        self.discard.setEnabled(not self._busy and self.is_dirty())
        self.archive.setEnabled(can_leave and isinstance(self._selected, Project))
        self.delete_area.setEnabled(can_leave and isinstance(self._selected, LifeArea))

    def is_dirty(self) -> bool:
        selected = self._selected
        return (
            self.name.text() != ("" if selected is None else selected.name)
            or self.notes.toPlainText() != ("" if selected is None else selected.notes)
            or (
                self.kind.currentIndex() == 0
                and self.area.currentData()
                != (selected.area_public_id if isinstance(selected, Project) else None)
            )
        )

    def _edited(self) -> None:
        if self._binding:
            return
        self.status.setText(
            "Save or discard your edits to switch records or archive." if self.is_dirty() else ""
        )
        self._buttons()

    def _discard(self) -> None:
        if self._busy:
            return
        if self._selected is None:
            self._reset_record()
        else:
            self._select(self.list.currentRow())

    def reject(self) -> None:
        if self._busy:
            return
        if self.is_dirty():
            self.status.setText("Save or discard your edits before closing.")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._busy or self.is_dirty():
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    def force_close(self) -> None:
        super().reject()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.kind,
            self.list,
            self.name,
            self.notes,
            self.area,
            self.new,
            self.save,
            self.close_button,
        ):
            widget.setEnabled(not busy)
        self._buttons()

    def _save(self) -> None:
        if self._busy:
            return
        if not self.name.text().strip() or len(self.notes.toPlainText()) > 10_000:
            self.status.setText("Enter a name and at most 10,000 characters of notes.")
            return
        edit: AreaEdit = AreaEdit(name=self.name.text(), notes=self.notes.toPlainText())
        if self.kind.currentIndex() == 0:
            area_id = self.area.currentData()
            edit = ProjectEdit(
                name=edit.name,
                notes=edit.notes,
                area_public_id=area_id if isinstance(area_id, str) else None,
            )
        selected = self._selected
        self.save_requested.emit(
            "project" if self.kind.currentIndex() == 0 else "area",
            None if selected is None else selected.public_id,
            None if selected is None else selected.revision,
            edit,
        )

    def _archive(self) -> None:
        if isinstance(self._selected, Project) and not self._busy and not self.is_dirty():
            self.archive_requested.emit(self._selected, self._selected.archived_at_utc is None)

    def _delete_area(self) -> None:
        if isinstance(self._selected, LifeArea) and not self._busy and not self.is_dirty():
            self.delete_area_requested.emit(self._selected)
