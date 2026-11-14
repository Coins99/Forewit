"""Local task chooser and guarded add-only scheduling review; no mail/AI dependency."""

import asyncio
from datetime import date, datetime
from typing import Protocol, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.projects import Project
from mailbrief.domain.scheduling import (
    PlanningCandidatePage,
    PlanningResult,
    PlanningReview,
    PlanningUndo,
    SchedulingProposal,
    SchedulingRequest,
    SchedulingTask,
    TaskDemand,
    Workload,
)
from mailbrief.services.scheduling import SchedulingChangedError, SchedulingKnowledgeError
from mailbrief.ui.calendar_layout import clock
from mailbrief.ui.date_picker import calendar_popup
from mailbrief.ui.deadline_text import day_text, moment_text
from mailbrief.ui.diagnostics import log_failure
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.event_editor import DateBox, qdate, qday
from mailbrief.ui.fields import PopupChoice, fill_project_choice, takes_wheel
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list


def _minutes(value: float) -> str:
    return f"{value:g} min"


def review_summary(shown: Workload, first_day: date, last_day: date, today: date) -> str:
    """The review's figures as labelled lines, every one shown even at zero."""
    first = day_text(first_day, today=today)
    days = first if first_day == last_day else f"{first} – {day_text(last_day, today=today)}"
    free = "unknown" if shown.free_minutes is None else _minutes(shown.free_minutes)
    tasks = "task" if shown.unknown_tasks == 1 else "tasks"
    return "\n".join(
        (
            days,
            f"Still to schedule: {_minutes(shown.uncovered_minutes)} · "
            f"{shown.unknown_tasks} {tasks} without an estimate · "
            f"{shown.repair_tasks} needing repair",
            f"Free time ahead: {free}",
            f"Busy: {_minutes(shown.timed_busy_minutes)} of timed events and buffers · "
            f"{shown.all_day_commitments} all-day commitments",
            f"Already scheduled: {_minutes(shown.scheduled_minutes)}, "
            f"{_minutes(shown.unique_scheduled_minutes)} of calendar time · "
            f"{_minutes(shown.future_reserved_minutes)} still ahead · "
            f"{_minutes(shown.outside_horizon_credit_minutes)} counted from outside these dates",
            f"Calendar: {shown.knowledge}",
            "Suggestions start on a quarter hour, at least five minutes ahead, for review. "
            "Aggregate minutes do not prove every task fits. "
            "Busy all-day events block their dates; planned time is not actual work.",
        )
    )


def block_times(start: datetime, end: datetime, zone: ZoneInfo, today: date) -> str:
    """A block's times in the app's format: "Thu Nov 12, 09:00–10:30", naming both days
    when it crosses midnight."""
    first = moment_text(start, zone, today=today)
    same_day = start.astimezone(zone).date() == end.astimezone(zone).date()
    last = clock(end, zone) if same_day else moment_text(end, zone, today=today)
    return f"{first}–{last}"


class PlanningBackend(Protocol):
    async def list_projects(self) -> tuple[Project, ...]: ...
    async def planning_candidates(
        self,
        first: date,
        last: date,
        *,
        query: str = "",
        offset: int = 0,
        needs_time: bool = True,
        project_id: str | None = None,
        focus_only: bool = False,
        focus_day: date | None = None,
    ) -> PlanningCandidatePage: ...
    async def review_schedule(self, request: SchedulingRequest) -> PlanningReview: ...
    async def apply_schedule(
        self,
        proposal: SchedulingProposal,
        selected: tuple[str, ...],
        command_id: str,
        *,
        acknowledged_knowledge: str | None = None,
    ) -> PlanningResult: ...
    async def undo_schedule(self, token: PlanningUndo, command_id: str) -> PlanningResult: ...


class _Minutes(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:
        if takes_wheel(self, event):
            super().wheelEvent(event)


class PlanningTaskPanel(QWidget):
    """Paged Needs time / All eligible chooser, shared by wide Calendar and narrow drawer."""

    manual_requested = Signal(str)
    review_requested = Signal(object)

    def __init__(self, parent: QWidget, backend: PlanningBackend) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setObjectName("planningTaskPanel")
        self.setAccessibleName("Tasks needing planned time")
        self.first = self.last = date(2026, 1, 1)
        self.focus_day = self.first
        self._generation = 0
        self._closed = False
        self._next: int | None = None
        self._reads: set[asyncio.Task[None]] = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.query = QLineEdit()
        self.query.setObjectName("planningTaskSearch")
        self.query.setAccessibleName("Search eligible tasks")
        self.query.setPlaceholderText("Find a task")
        self.query.setMaxLength(200)
        layout.addWidget(self.query)
        self.mode = PopupChoice()
        self.mode.setObjectName("planningTaskMode")
        self.mode.setAccessibleName("Task planning filter")
        self.mode.addItem("Needs time", True)
        self.mode.addItem("All eligible tasks", False)
        layout.addWidget(self.mode)
        self.projects = PopupChoice()
        self.projects.setObjectName("planningTaskProject")
        self.projects.setAccessibleName("Filter tasks by project")
        self.projects.addItem("All projects", None)
        self.projects.setMinimumContentsLength(12)
        self.projects.setSizeAdjustPolicy(
            PopupChoice.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        layout.addWidget(self.projects)
        self.focus_only = QCheckBox("My focus only")
        self.focus_only.setObjectName("planningFocusOnly")
        self.focus_only.setAccessibleName(self.focus_only.text())
        layout.addWidget(self.focus_only)
        # Above the list, where an owner looks: the status line can be below the fold.
        self.empty = wrap_label("", tone="muted")
        self.empty.setObjectName("planningNoCandidates")
        self.empty.setAccessibleName("No eligible tasks")
        self.empty.hide()
        layout.addWidget(self.empty)
        self.tasks = ActivatingList()
        self.tasks.setObjectName("planningCandidates")
        self.tasks.setAccessibleName("Eligible tasks with known and unknown remaining duration")
        self.tasks.setWordWrap(True)
        self.tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tasks.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tasks.itemActivated.connect(lambda: self._review())
        layout.addWidget(self.tasks, 1)
        self.status = wrap_label("", tone="muted")
        self.status.setObjectName("planningCandidatesStatus")
        self.status.setAccessibleName("Task candidate loading status")
        self.status.setMinimumHeight(38)
        layout.addWidget(self.status)
        self.more_button = outline_button("More tasks", "planningMoreTasks")
        self.manual_button = outline_button("Manual block", "planningManualBlock")
        self.review_button = outline_button("Plan selected", "planningReviewTasks")
        for button in (self.more_button, self.manual_button, self.review_button):
            button.setAccessibleName(button.text())
            layout.addWidget(button)
        self.more_button.clicked.connect(lambda: self.load(more=True))
        self.manual_button.clicked.connect(self._manual)
        self.review_button.clicked.connect(self._review)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.load)
        self.query.textChanged.connect(lambda: self.timer.start())
        self.mode.currentIndexChanged.connect(self.load)
        self.projects.currentIndexChanged.connect(self.load)
        self.focus_only.toggled.connect(self.load)

    def show_range(self, first: date, last: date, focus_day: date | None = None) -> None:
        self.first, self.last = first, last
        self.focus_day = (
            focus_day if focus_day is not None and first <= focus_day <= last else first
        )
        self._closed = False
        self.load()

    def load(self, _changed: object = None, *, more: bool = False) -> None:
        if self._closed:
            return
        self._generation += 1
        for pending in tuple(self._reads):
            pending.cancel()
        generation = self._generation
        offset = (self._next or 0) if more else 0
        query, needs_time = self.query.text(), self.mode.currentData()
        project, focus = self.projects.currentData(), self.focus_only.isChecked()
        self.more_button.setEnabled(False)
        self.status.setText("Loading local task planning…")

        async def read() -> None:
            try:
                page = await self.backend.planning_candidates(
                    self.first,
                    self.last,
                    query=query,
                    offset=offset,
                    needs_time=bool(needs_time),
                    project_id=project,
                    focus_only=focus,
                    focus_day=self.focus_day,
                )
                projects = (
                    await self.backend.list_projects() if self.projects.count() == 1 else None
                )
                if self._closed or generation != self._generation:
                    return
                if projects is not None:
                    self.projects.blockSignals(True)
                    fill_project_choice(self.projects, projects, project, none_label="All projects")
                    self.projects.blockSignals(False)
                selected_ids = {demand.task_public_id for demand in self.selected()}
                if not more:
                    clear_list(self.tasks)
                existing_ids = {
                    self.tasks.item(index).data(Qt.ItemDataRole.UserRole).task_public_id
                    for index in range(self.tasks.count())
                }
                for demand in page.items:
                    if demand.task_public_id in existing_ids:
                        continue
                    uncovered = demand.uncovered_seconds
                    label = (
                        "Duration unknown" if uncovered is None else f"Needs {uncovered / 60:g} min"
                    )
                    if demand.needs_repair:
                        label += " · Needs schedule repair"
                    item = QListWidgetItem(
                        f"{demand.title}\n{label} · {demand.credited_seconds / 60:g} min credited"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, demand)
                    self.tasks.addItem(item)
                    if demand.task_public_id in selected_ids:
                        item.setSelected(True)
                self._next = page.next_offset
                self.more_button.setEnabled(self._next is not None)
                self.empty.setText(
                    "No task matches the search or filters."
                    if query.strip() or project or focus
                    else "No tasks found for these dates."
                )
                self.empty.setVisible(self.tasks.count() == 0)
                self.status.setText(
                    f"{self.first.isoformat()} – {self.last.isoformat()} · "
                    "Credit can include later dates."
                )
            except Exception as exc:
                log_failure(exc)
                if not self._closed and generation == self._generation:
                    self.empty.hide()
                    self.status.setText(
                        "Task planning could not be loaded. Change the filter to retry."
                    )

        task = asyncio.get_running_loop().create_task(read())
        self._reads.add(task)
        task.add_done_callback(self._reads.discard)

    def selected(self) -> tuple[TaskDemand, ...]:
        return tuple(item.data(Qt.ItemDataRole.UserRole) for item in self.tasks.selectedItems())

    def _manual(self) -> None:
        selected = self.selected()
        if len(selected) == 1:
            self.manual_requested.emit(selected[0].task_public_id)
        else:
            self.status.setText("Choose one task for a manual block.")

    def _review(self) -> None:
        selected = self.selected()
        if selected:
            self.review_requested.emit(selected)

    def force_close(self) -> None:
        self._closed = True
        self._generation += 1
        self.timer.stop()

    async def drain(self) -> None:
        if self._reads:
            await asyncio.gather(*tuple(self._reads), return_exceptions=True)


class SchedulingDialog(QDialog):
    changed = Signal()

    def __init__(self, parent: QWidget, backend: PlanningBackend) -> None:
        super().__init__(parent)
        self.backend = backend
        self.setObjectName("schedulingDialog")
        self.setAccessibleName("Local task scheduling review")
        self.setWindowTitle("Plan task time")
        self.setProperty("presentation", "drawer")
        self.resize(470, 680)
        self._closed = True
        self._generation = 0
        self._reads: set[asyncio.Task[None]] = set()
        self._write: asyncio.Task[None] | None = None
        self._choices: dict[str, SchedulingTask] = {}
        self._titles: dict[str, str] = {}
        self._review: PlanningReview | None = None
        self._undo: PlanningUndo | None = None
        self._pending: (
            tuple[SchedulingProposal | PlanningUndo, tuple[str, ...], str, str | None] | None
        ) = None
        self._uncertain = False
        self._knowledge_token: str | None = None
        self._filling = False
        layout = QVBoxLayout(self)
        dialog_heading(
            self,
            layout,
            "Plan task time",
            "Local suggestions add blocks; commitments and task dates stay fixed.",
        )
        scroll = QScrollArea()
        self.form_scroll = scroll
        scroll.setObjectName("schedulingScroll")
        scroll.setAccessibleName("Task scheduling form and preview")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body.setObjectName("schedulingBody")
        self.body.setAccessibleName("Task scheduling choices")
        content = QVBoxLayout(self.body)
        dates = QFormLayout()
        self.first_day = DateBox()
        self.last_day = DateBox()
        for name, field in (("First date", self.first_day), ("Last date", self.last_day)):
            field.setObjectName("scheduling" + name.replace(" ", ""))
            field.setAccessibleName(name + " for suggestions")
            field.setDisplayFormat("yyyy-MM-dd")
            calendar_popup(field)
            field.dateChanged.connect(self._range_changed)
            dates.addRow(name, field)
        content.addLayout(dates)
        self.focused_day = QCheckBox("Plan the focused day only")
        self.focused_day.setObjectName("schedulingFocusedDay")
        self.focused_day.setAccessibleName(self.focused_day.text())
        content.addWidget(self.focused_day)
        self.chooser = PlanningTaskPanel(self.body, backend)
        self.chooser.setMinimumHeight(300)
        self.chooser.review_button.setText("Add selected tasks")
        self.chooser.review_requested.connect(self._add)
        self.chooser.manual_button.hide()
        content.addWidget(self.chooser)
        self.chosen = ActivatingList()
        self.chosen.setObjectName("schedulingChosenTasks")
        self.chosen.setAccessibleName("Tasks chosen for this suggestion")
        self.chosen.setMaximumHeight(110)
        self.chosen.currentRowChanged.connect(self._fill_choice)
        self.edit_fields = QWidget()
        self.edit_fields.setObjectName("schedulingTaskOptions")
        self.edit_fields.setAccessibleName("Selected task durations and split options")
        edit_layout = QVBoxLayout(self.edit_fields)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addWidget(self.chosen)
        form = QFormLayout()
        self.minutes = _Minutes()
        self.minutes.setObjectName("schedulingRemainingMinutes")
        self.minutes.setAccessibleName("Total remaining minutes before crediting future blocks")
        self.minutes.setRange(0, 10080)
        self.minutes.setSpecialValueText("Use saved estimate")
        form.addRow("Remaining minutes", self.minutes)
        self.split = QCheckBox("Allow splitting (up to four pieces)")
        self.split.setObjectName("schedulingAllowSplit")
        self.split.setAccessibleName(self.split.text())
        form.addRow(self.split)
        self.minimum = _Minutes()
        self.minimum.setObjectName("schedulingMinimumPiece")
        self.minimum.setAccessibleName("Minimum split piece in minutes")
        self.minimum.setRange(5, 720)
        self.minimum.setValue(15)
        form.addRow("Minimum piece", self.minimum)
        self.remainder = QCheckBox("Allow a smaller final remainder")
        self.remainder.setObjectName("schedulingSmallRemainder")
        self.remainder.setAccessibleName(self.remainder.text())
        form.addRow(self.remainder)
        edit_layout.addLayout(form)
        self.remove_button = outline_button("Remove task", "schedulingRemoveTask")
        self.remove_button.setAccessibleName(self.remove_button.text())
        self.remove_button.clicked.connect(self._remove)
        edit_layout.addWidget(self.remove_button)
        content.addWidget(self.edit_fields)
        self.summary = wrap_label("No suggestion yet. Choose tasks and review before applying.")
        self.summary.setObjectName("schedulingWorkload")
        self.summary.setAccessibleName("Known workload and capacity limits")
        content.addWidget(self.summary)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("schedulingPreview")
        self.preview.setAccessibleName("Suggested blocks, unplaced tasks and conflict reasons")
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(140)
        content.addWidget(self.preview)
        self.apply_tasks = ActivatingList()
        self.apply_tasks.setObjectName("schedulingApplyTasks")
        self.apply_tasks.setAccessibleName("Whole proposed tasks to apply")
        self.apply_tasks.setMaximumHeight(110)
        content.addWidget(self.apply_tasks)
        self.ack = QCheckBox("Use known commitments only")
        self.ack.setObjectName("schedulingKnowledgeAcknowledgement")
        self.ack.setAccessibleName(self.ack.text())
        self.ack.hide()
        content.addWidget(self.ack)
        scroll.setWidget(self.body)
        layout.addWidget(scroll, 1)
        self.status = wrap_label("", tone="muted")
        self.status.setObjectName("schedulingStatus")
        self.status.setAccessibleName("Scheduling save status")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.review_button = outline_button("Review", "schedulingGenerate")
        self.apply_button = outline_button("Apply", "schedulingApply", primary=True)
        self.apply_button.setAccessibleName("Apply selected task blocks")
        self.undo_button = outline_button("Undo", "schedulingUndo")
        self.close_button = outline_button("Close", "schedulingClose")
        for button in (self.review_button, self.apply_button, self.undo_button, self.close_button):
            button.setAccessibleName(button.text())
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.discard_button = outline_button("Discard selection and preview", "schedulingDiscard")
        self.discard_button.setAccessibleName(self.discard_button.text())
        self.discard_button.clicked.connect(self._discard)
        layout.addWidget(self.discard_button)
        self.review_button.clicked.connect(self._generate)
        self.apply_button.clicked.connect(self._apply)
        self.undo_button.clicked.connect(self._request_undo)
        self.close_button.clicked.connect(self.reject)
        self.focused_day.toggled.connect(self._invalidate)
        self.minutes.valueChanged.connect(self._choice_changed)
        self.minimum.valueChanged.connect(self._choice_changed)
        self.split.toggled.connect(self._choice_changed)
        self.remainder.toggled.connect(self._choice_changed)
        self._controls()

    def open_range(
        self, first: date, last: date, focus: date, demands: tuple[TaskDemand, ...] = ()
    ) -> None:
        if self.has_unsaved_input():
            self.status.setText("Apply or discard the existing preview first.")
            return
        self._closed = False
        self._generation += 1
        for field, day in ((self.first_day, first), (self.last_day, last)):
            field.blockSignals(True)
            field.setDate(qdate(day))
            field.blockSignals(False)
        self.chooser.show_range(first, last, focus)
        self._add(demands)

    def _range_changed(self) -> None:
        if self._closed or self._write is not None or self._uncertain:
            return
        first, last = qday(self.first_day.date()), qday(self.last_day.date())
        self._invalidate()
        if not 0 <= (last - first).days < 42:
            self.status.setText("Choose between one and 42 dates.")
            self.review_button.setEnabled(False)
            return
        self.chooser.show_range(first, last, first)

    def _invalidate(self) -> None:
        self._generation += 1
        self._review = None
        self._knowledge_token = None
        self.ack.setChecked(False)
        self.ack.hide()
        self.preview.clear()
        clear_list(self.apply_tasks)
        self._controls()

    def _add(self, values: object) -> None:
        if not isinstance(values, tuple) or self._write is not None or self._uncertain:
            return
        for demand in values:
            if isinstance(demand, TaskDemand) and demand.task_public_id not in self._choices:
                if len(self._choices) == 100:
                    self.status.setText("Choose at most 100 tasks per suggestion.")
                    break
                self._choices[demand.task_public_id] = SchedulingTask(
                    task_public_id=demand.task_public_id
                )
                self._titles[demand.task_public_id] = demand.title
                item = QListWidgetItem(demand.title)
                item.setData(Qt.ItemDataRole.UserRole, demand.task_public_id)
                self.chosen.addItem(item)
        self._invalidate()
        if self.chosen.currentRow() < 0 and self.chosen.count():
            self.chosen.setCurrentRow(0)

    def _fill_choice(self, _row: int = -1) -> None:
        item = cast(QListWidgetItem | None, self.chosen.currentItem())
        if item is None:
            return
        choice = self._choices[item.data(Qt.ItemDataRole.UserRole)]
        self._filling = True
        self.minutes.setValue(choice.remaining_minutes or 0)
        self.split.setChecked(choice.split)
        self.minimum.setValue(choice.minimum_piece_minutes)
        self.remainder.setChecked(choice.allow_small_remainder)
        self._filling = False

    def _choice_changed(self) -> None:
        item = cast(QListWidgetItem | None, self.chosen.currentItem())
        if item is None or self._filling or self._write is not None or self._uncertain:
            return
        task_id = item.data(Qt.ItemDataRole.UserRole)
        self._choices[task_id] = SchedulingTask(
            task_public_id=task_id,
            remaining_minutes=self.minutes.value() or None,
            split=self.split.isChecked(),
            minimum_piece_minutes=self.minimum.value(),
            allow_small_remainder=self.remainder.isChecked(),
        )
        self._invalidate()

    def _remove(self) -> None:
        item = cast(QListWidgetItem | None, self.chosen.currentItem())
        if item is not None:
            self._choices.pop(item.data(Qt.ItemDataRole.UserRole))
            self.chosen.takeItem(self.chosen.row(item))
            self._invalidate()

    def _generate(self) -> None:
        if not 0 <= (qday(self.last_day.date()) - qday(self.first_day.date())).days < 42:
            self.status.setText("Choose between one and 42 dates.")
            return
        if self._review is not None and self._write is None and not self._uncertain:
            self._invalidate()
            self.form_scroll.verticalScrollBar().setValue(0)
            return
        if self._write is not None or self._uncertain or not self._choices:
            return
        self._invalidate()
        generation = self._generation
        request = SchedulingRequest(
            first_day=self.chooser.focus_day
            if self.focused_day.isChecked()
            else self.chooser.first,
            last_day=self.chooser.focus_day if self.focused_day.isChecked() else self.chooser.last,
            focus_day=self.chooser.focus_day,
            tasks=tuple(self._choices.values()),
        )
        self.status.setText("Reading local planning inputs…")

        async def read() -> None:
            try:
                review = await self.backend.review_schedule(request)
                if self._closed or generation != self._generation:
                    return
                self._review = review
                self._titles.update(
                    {demand.task_public_id: demand.title for demand in review.snapshot.demands}
                )
                zone = ZoneInfo(review.snapshot.owner_zone)
                shown = review.workload
                today = review.snapshot.captured_at_utc.astimezone(zone).date()
                self.summary.setText(
                    review_summary(shown, request.first_day, request.last_day, today)
                )
                lines = []
                proposed = set()
                for block in review.proposal.blocks:
                    proposed.add(block.task_public_id)
                    lines.append(
                        f"Task block · {self._titles[block.task_public_id]}\n"
                        f"{block_times(block.starts_at_utc, block.ends_at_utc, zone, today)}"
                    )
                lines.extend(
                    f"Not placed · {self._titles.get(item.task_public_id, 'Task')}: {item.reason}"
                    for item in review.proposal.unplaced
                )
                lines.extend(
                    "Existing block warning · " + message
                    for conflict in shown.conflicts
                    for message in conflict.messages
                )
                self.preview.setPlainText(
                    "\n\n".join(lines) or "Selected work already has valid future reservations."
                )
                for task_id in self._choices:
                    if task_id in proposed:
                        item = QListWidgetItem(self._titles[task_id])
                        item.setData(Qt.ItemDataRole.UserRole, task_id)
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Checked)
                        self.apply_tasks.addItem(item)
                self.status.setText(
                    "Review whole tasks, times and knowledge limits before applying."
                )
                self._controls()
            except Exception as exc:
                log_failure(exc)
                if not self._closed and generation == self._generation:
                    self.status.setText(
                        "Suggestion could not be read. Your selection is kept; try Review again."
                    )

        task = asyncio.get_running_loop().create_task(read())
        self._reads.add(task)
        task.add_done_callback(self._reads.discard)

    def _apply(self) -> None:
        if self._write is not None:
            return
        if self._uncertain and self._pending is not None:
            self._send(self._pending)
            return
        if self._review is None:
            return
        selected = tuple(
            self.apply_tasks.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.apply_tasks.count())
            if self.apply_tasks.item(index).checkState() == Qt.CheckState.Checked
        )
        if not selected:
            self.status.setText("Tick at least one whole proposed task.")
            return
        self._send(
            (
                self._review.proposal,
                selected,
                str(uuid4()),
                self._knowledge_token if self.ack.isChecked() else None,
            )
        )

    def _request_undo(self) -> None:
        if self._write is None and self._undo is not None and not self._uncertain:
            self._send((self._undo, (), str(uuid4()), None))

    def _send(
        self, pending: tuple[SchedulingProposal | PlanningUndo, tuple[str, ...], str, str | None]
    ) -> None:
        self._pending = pending

        async def write() -> None:
            value, selected, key, acknowledgement = pending
            try:
                if isinstance(value, SchedulingProposal):
                    result = await self.backend.apply_schedule(
                        value, selected, key, acknowledged_knowledge=acknowledgement
                    )
                else:
                    result = await self.backend.undo_schedule(value, key)
                self._pending = None
                self._uncertain = False
                self._undo = result.undo
                if not self._closed:
                    self._choices.clear()
                    clear_list(self.chosen)
                    self._invalidate()
                    self.status.setText(
                        "Planning change saved."
                        if not result.replayed
                        else "Previous planning save confirmed."
                    )
                    self.changed.emit()
                    self.chooser.load()
            except SchedulingKnowledgeError as exc:
                self._pending = None
                self._uncertain = False
                if not self._closed:
                    self._knowledge_token = exc.token
                    self.ack.setChecked(False)
                    self.ack.show()
                    self.status.setText(
                        f"Calendar knowledge: {exc.status}. "
                        "Review the limitation and tick acknowledgement to apply."
                    )
            except SchedulingChangedError as exc:
                self._pending = None
                self._uncertain = False
                if not self._closed:
                    self._review = None
                    self.status.setText(str(exc))
            except Exception as exc:
                log_failure(exc)
                self._uncertain = True
                if not self._closed:
                    self.status.setText(
                        "Save could not be confirmed. Edits are frozen; "
                        "Retry save sends the same command."
                    )
            finally:
                self._write = None
                if not self._closed:
                    self._controls()

        self._write = asyncio.get_running_loop().create_task(write())
        self._controls()

    def _controls(self) -> None:
        busy = self._write is not None
        reviewing = self._review is not None
        self.focused_day.setVisible(not reviewing)
        self.chooser.setVisible(not reviewing)
        self.edit_fields.setVisible(not reviewing)
        self.summary.setVisible(reviewing)
        self.preview.setVisible(reviewing)
        self.apply_tasks.setVisible(reviewing)
        self.review_button.setText("Edit tasks" if reviewing else "Review")
        self.body.setEnabled(not busy and not self._uncertain)
        self.apply_button.setText("Retry save" if self._uncertain else "Apply")
        self.apply_button.setEnabled(
            not busy
            and (
                self._uncertain or (self._review is not None and bool(self._review.proposal.blocks))
            )
        )
        self.review_button.setEnabled(not busy and not self._uncertain and bool(self._choices))
        self.undo_button.setEnabled(
            not busy and not self._uncertain and self._undo is not None and not self._choices
        )
        self.discard_button.setEnabled(not busy and not self._uncertain)
        self.close_button.setEnabled(not busy)

    def _discard(self) -> None:
        if self._write is not None or self._uncertain:
            return
        self._choices.clear()
        clear_list(self.chosen)
        self._invalidate()
        self.status.setText("Selection and preview discarded; saved blocks unchanged.")

    def has_unsaved_input(self) -> bool:
        return bool(self._choices) or self._uncertain or self._write is not None

    def reject(self) -> None:
        if self.has_unsaved_input():
            self.status.setText("Apply or discard the selection and preview before closing.")
            return
        self.force_close()

    def force_close(self) -> None:
        self._closed = True
        self._generation += 1
        self.chooser.force_close()
        super().reject()

    async def drain(self) -> None:
        if self._write is not None:
            await asyncio.shield(self._write)
        await self.chooser.drain()
        if self._reads:
            await asyncio.gather(*tuple(self._reads), return_exceptions=True)
